#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""From pending test-case operations to a complete staged directory.

The editor's Save never edits the problem's live test-case directory. It builds
the *whole* desired directory in staging -- carried files included -- and the
:class:`~shared.services.problem_package.edit_swap.EditArtifactSwap` renames it
in once the transaction is ready to commit. That is what makes a failed Save
recoverable: there is exactly one rename to undo, and the author's previous
directory is quarantined rather than deleted.

Two halves, deliberately separated so the interesting one needs no filesystem:

* :func:`build_desired_cases` is pure. Given the problem's current cases and the
  parsed operations, it decides what the final list looks like and where each
  entry's content comes from.
* :func:`materialize` applies that decision inside a staging directory that has
  already been seeded with a copy of the current files.

Ordinals are the join between the two: a case's content lives at ``NNN.in`` /
``NNN.out``, so moving a case is renaming a file, and the plan carries both the
source ordinal and the final one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from shared.services.problem_save_errors import PendingOpsError
from shared.services.testcase_files import write_testcase_files_into
from shared.services.testcase_pending_ops import CaseContent, PendingTestCaseOps

#: Prefix of the temporary names used while permuting the staged directory.
_TEMP_PREFIX = ".noca-plan-"


class MissingCarriedCase(PendingOpsError):
    """A case the action carries unchanged has no input file on disk.

    Raised rather than papered over: the alternative is committing an empty test
    case in place of one whose storage is damaged, during an action that was about
    a different case entirely.
    """


class CaseOrigin(StrEnum):
    """Where one desired case's content comes from."""

    CARRIED = "carried"
    REPLACED = "replaced"
    ADDED = "added"


@dataclass(frozen=True, slots=True)
class CurrentCase:
    """One test case as the problem currently stores it.

    Attributes:
        id: The row's primary key.
        ordinal: Its 1-based position, which is also its filename stem.
        is_sample: Whether contestants currently see it.
    """

    id: str
    ordinal: int
    is_sample: bool


@dataclass(frozen=True, slots=True)
class DesiredCase:
    """One case in the Save's final list.

    Attributes:
        origin: Whether the content is carried from the current directory,
            replaced by an upload, or entirely new.
        ordinal: The final 1-based position.
        source_id: The existing row this case belongs to, or ``None`` when the
            Save creates it.
        source_ordinal: Where the content currently lives, for a carried or
            replaced case.
        content: The new content, for a replaced or added case.
        is_sample: The final sample flag.
        explanation: The explanation to store, when this case sets one.
        set_explanation: Whether ``explanation`` replaces the stored value. A
            replacement archive with no ``explanation.txt`` leaves the author's
            existing text alone rather than erasing it.
    """

    origin: CaseOrigin
    ordinal: int
    source_id: str | None
    source_ordinal: int | None
    content: CaseContent | None
    is_sample: bool
    explanation: str | None = None
    set_explanation: bool = False


@dataclass(frozen=True, slots=True)
class MaterializedCase:
    """One desired case after its content reached the staging directory.

    Attributes:
        plan: The decision this came from.
        input_size_bytes: Normalized on-disk size of the input.
        output_size_bytes: Normalized on-disk size of the expected output, or
            ``None`` for a case that has none.
    """

    plan: DesiredCase
    input_size_bytes: int
    output_size_bytes: int | None


def build_desired_cases(
    current: Sequence[CurrentCase],
    ops: PendingTestCaseOps,
    *,
    interactive: bool,
) -> list[DesiredCase]:
    """Decide the Save's final test-case list.

    A replace-all archive discards the current list outright; otherwise the
    surviving cases keep their relative order, replacements happen in place, and
    additions are appended. Ordinals are renumbered contiguously either way, so
    removing case 2 of 3 leaves cases 1 and 2, never a gap.

    Args:
        current: The problem's cases, in ordinal order.
        ops: The parsed operations.
        interactive: Whether the problem's stored strategy is interactive, in
            which case every case is secret and carries no expected output.

    Returns:
        list[DesiredCase]: The final list, in final order.
    """
    if ops.bulk_cases is not None:
        return [
            DesiredCase(
                origin=CaseOrigin.ADDED,
                ordinal=position,
                source_id=None,
                source_ordinal=None,
                content=case,
                is_sample=not interactive and case.is_sample,
                explanation=case.explanation,
                set_explanation=True,
            )
            for position, case in enumerate(ops.bulk_cases, start=1)
        ]

    desired: list[DesiredCase] = []
    position = 0
    for case in _in_desired_order(current, ops.order):
        if case.id in ops.removals:
            continue
        position += 1
        is_sample = case.is_sample if not interactive else False
        if case.id in ops.sample_toggles:
            is_sample = not is_sample
        replacement = ops.replacements.get(case.id)
        if replacement is None:
            desired.append(
                DesiredCase(
                    origin=CaseOrigin.CARRIED,
                    ordinal=position,
                    source_id=case.id,
                    source_ordinal=case.ordinal,
                    content=None,
                    is_sample=is_sample,
                )
            )
            continue
        # A replacement that states its metadata (an inline edit) decides both
        # fields, including clearing the explanation; one that does not (a ZIP,
        # which carries neither a sample flag nor a required explanation) leaves
        # what the row holds.
        if replacement.states_metadata:
            is_sample = replacement.is_sample and not interactive
        desired.append(
            DesiredCase(
                origin=CaseOrigin.REPLACED,
                ordinal=position,
                source_id=case.id,
                source_ordinal=case.ordinal,
                content=replacement,
                is_sample=is_sample,
                explanation=replacement.explanation,
                set_explanation=replacement.states_metadata or replacement.explanation is not None,
            )
        )

    for added in ops.added:
        position += 1
        desired.append(
            DesiredCase(
                origin=CaseOrigin.ADDED,
                ordinal=position,
                source_id=None,
                source_ordinal=None,
                content=added,
                is_sample=not interactive and added.is_sample,
                explanation=added.explanation,
                set_explanation=True,
            )
        )
    return desired


def _in_desired_order(current: Sequence[CurrentCase], order: tuple[str, ...] | None) -> list[CurrentCase]:
    """Return the current cases in the order the plan wants them.

    An id in ``order`` that names no current case is ignored, and a current case
    the order forgot keeps its place at the end: the plan describes what the
    author asked for, and a stale page must not be able to make a case vanish by
    omission.

    Args:
        current: The problem's cases.
        order: The requested id sequence, or ``None`` to keep stored order.

    Returns:
        list[CurrentCase]: The cases in final relative order.
    """
    by_ordinal = sorted(current, key=lambda item: item.ordinal)
    if order is None:
        return by_ordinal
    known = {case.id: case for case in by_ordinal}
    ordered = [known[case_id] for case_id in order if case_id in known]
    claimed = {case.id for case in ordered}
    ordered.extend(case for case in by_ordinal if case.id not in claimed)
    return ordered


def materialize(
    desired: Sequence[DesiredCase],
    staged_dir: Path,
    *,
    interactive: bool = False,
) -> list[MaterializedCase]:
    """Write the desired list into an already-seeded staging directory.

    ``staged_dir`` starts as a copy of the problem's current files (that is what
    :meth:`~shared.services.problem_package.edit_swap.EditArtifactSwap.stage_test_cases`
    hands back), so this only has to permute, overwrite and prune it. Every
    carried file is first moved aside under a temporary name, because a Save that
    reorders cases would otherwise overwrite a file it still needs.

    Args:
        desired: The final list, in final order.
        staged_dir: The staging directory to bring into that shape.
        interactive: Whether the problem's stored strategy is interactive, in
            which case a carried case's expected-output file is dropped -- an
            interactive case has no expected output, and a stale ``.out`` left
            over from before the strategy was stored would be a lie on disk.

    Returns:
        list[MaterializedCase]: One entry per desired case, with the on-disk
        sizes the database rows must record.
    """
    staged_dir.mkdir(parents=True, exist_ok=True)
    _park_existing(staged_dir)

    materialized: list[MaterializedCase] = []
    for case in desired:
        if case.origin is CaseOrigin.CARRIED and case.source_ordinal is not None:
            sizes = _restore_parked(staged_dir, case.source_ordinal, case.ordinal, drop_output=interactive)
        else:
            assert case.content is not None
            sizes = write_testcase_files_into(
                staged_dir,
                case.ordinal,
                case.content.input_bytes,
                case.content.output_bytes,
            )
        materialized.append(MaterializedCase(plan=case, input_size_bytes=sizes[0], output_size_bytes=sizes[1]))

    _drop_parked(staged_dir)
    return materialized


def _park_existing(staged_dir: Path) -> None:
    """Move every seeded case file aside, keyed by its current ordinal."""
    for path in sorted(staged_dir.iterdir()):
        if not path.is_file() or path.suffix not in {".in", ".out"} or path.name.startswith(_TEMP_PREFIX):
            continue
        path.rename(staged_dir / f"{_TEMP_PREFIX}{path.name}")


def _restore_parked(
    staged_dir: Path,
    source_ordinal: int,
    ordinal: int,
    *,
    drop_output: bool,
) -> tuple[int, int | None]:
    """Move one parked case back under its final ordinal.

    Returns:
        tuple[int, int | None]: The on-disk sizes of the restored files.

    Raises:
        MissingCarriedCase: If the case's input file is not there. It used to
            materialize as an empty case, which turns storage corruption into a
            committed zero-byte test case -- silently, while the author was
            editing some *other* case. Failing the action leaves the problem
            exactly as it was; replacing that specific case is still the way out,
            because a replacement brings its own content and carries nothing.
    """
    input_size = 0
    output_size: int | None = None
    for ext in ("in", "out"):
        parked = staged_dir / f"{_TEMP_PREFIX}{source_ordinal:03d}.{ext}"
        final = staged_dir / f"{ordinal:03d}.{ext}"
        if not parked.exists():
            continue
        if ext == "out" and drop_output:
            parked.unlink()
            continue
        parked.rename(final)
        if ext == "in":
            input_size = final.stat().st_size
        else:
            output_size = final.stat().st_size
    if not (staged_dir / f"{ordinal:03d}.in").exists():
        raise MissingCarriedCase(
            f"Test case #{source_ordinal} has no input file on disk. "
            "Replace that case from a ZIP to restore it; other cases are unchanged."
        )
    if not drop_output and not (staged_dir / f"{ordinal:03d}.out").exists():
        raise MissingCarriedCase(
            f"Test case #{source_ordinal} has no expected-output file on disk. "
            "Replace that case from a ZIP to restore it; other cases are unchanged."
        )
    return input_size, output_size


def _drop_parked(staged_dir: Path) -> None:
    """Delete every parked file no desired case claimed."""
    for path in list(staged_dir.iterdir()):
        if path.is_file() and path.name.startswith(_TEMP_PREFIX):
            path.unlink(missing_ok=True)
