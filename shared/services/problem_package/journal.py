#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Crash-safe journal for problem-package artifact promotion.

Promotion spans two different roots (statements and test cases) and mixes files
with directories, so a marker written *inside* the promoted directory cannot
describe it. One guarded journal file per import instead records every staged
source and its final target, is ``fsync``'d and renamed into place at each state
transition, and is deleted on success.

Reconciliation runs at application startup and before each import, and resolves
two kinds of journal by two different commit signals:

* an **import** journal looks the problem row up. If it exists the commit won and
  the journal is merely cleared; if it does not, the recorded artifacts are
  deleted.
* an **edit** journal cannot use existence — an edited problem exists either way
  — so it records the ``artifact_generation`` value the Save expects the row to
  hold after committing, and recovery compares that against the stored value.

Every path read from a journal is re-validated against its configured root before
anything is deleted or restored, so a corrupted or tampered journal can never
direct a delete outside the problem storage roots.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from shared.services.durable_fs import fsync_directory
from shared.services.problem_package.journal_model import (
    JOURNAL_SUFFIX,
    JOURNAL_VERSION,
    SUPPORTED_JOURNAL_VERSIONS,
    ImportJournal,
    JournalEntry,
    JournalKind,
    PromotionState,
)
from shared.services.problem_package.journal_recovery import (
    EditGuard,
    GenerationLookup,
    delete_orphans,
    resolve_edit_journal,
)
from shared.services.problem_package.staging import PlannedArtifact

logger = logging.getLogger(__name__)

__all__ = [
    "JOURNAL_DIRNAME",
    "JOURNAL_SUFFIX",
    "JOURNAL_VERSION",
    "SUPPORTED_JOURNAL_VERSIONS",
    "ImportJournal",
    "JournalEntry",
    "JournalKind",
    "PromotionState",
    "build_edit_journal",
    "build_journal",
    "clear_journal",
    "journal_path",
    "journal_root_for",
    "read_journal",
    "reconcile_journals",
    "write_journal",
]


JOURNAL_DIRNAME = ".noca-import-journals"


def journal_root_for(testcase_dir: Path) -> Path:
    """Return the journal directory for one domain's test-case root.

    The journals live inside the test-case root so they share its filesystem and
    need no configuration of their own. The leading dot keeps the directory out
    of the problem-id namespace, which
    :func:`shared.services.testcase_files.get_problem_testcase_dir` restricts to
    names that cannot start with one.
    """
    return testcase_dir / JOURNAL_DIRNAME


def journal_path(journal_root: Path, token: str) -> Path:
    """Return the journal file path for one import token."""
    return journal_root / f"{token}{JOURNAL_SUFFIX}"


def write_journal(journal: ImportJournal) -> None:
    """Atomically write a journal, flushing it to stable storage first."""
    journal.path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": JOURNAL_VERSION,
        "problem_id": journal.problem_id,
        "domain": journal.domain,
        "state": journal.state.value,
        "kind": journal.kind.value,
        "expected_generation": journal.expected_generation,
        "entries": [
            {
                "staged": str(entry.staged),
                "target": str(entry.target),
                "root": str(entry.root),
                "quarantine": None if entry.quarantine is None else str(entry.quarantine),
                "target_existed": entry.target_existed,
                "removal": entry.removal,
            }
            for entry in journal.entries
        ],
    }
    handle, temp_name = tempfile.mkstemp(dir=journal.path.parent, prefix=".journal-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, journal.path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    fsync_directory(journal.path.parent)


def build_journal(
    journal_root: Path,
    token: str,
    *,
    problem_id: str,
    domain: str,
    artifacts: list[PlannedArtifact],
) -> ImportJournal:
    """Build the initial ``staged`` journal for one import attempt."""
    return ImportJournal(
        path=journal_path(journal_root, token),
        problem_id=problem_id,
        domain=domain,
        entries=tuple(JournalEntry(staged=item.staged, target=item.target, root=item.root) for item in artifacts),
        state=PromotionState.STAGED,
        kind=JournalKind.IMPORT,
    )


def build_edit_journal(
    journal_root: Path,
    token: str,
    *,
    problem_id: str,
    domain: str,
    artifacts: list[PlannedArtifact],
    expected_generation: int,
) -> ImportJournal:
    """Build the initial ``staged`` journal for one editor Save.

    Args:
        expected_generation: The ``artifact_generation`` the problem row holds
            once this Save commits. Recovery compares it against the stored value
            because an edited problem exists whether or not the commit landed.
    """
    return ImportJournal(
        path=journal_path(journal_root, token),
        problem_id=problem_id,
        domain=domain,
        entries=tuple(JournalEntry(staged=item.staged, target=item.target, root=item.root) for item in artifacts),
        state=PromotionState.STAGED,
        kind=JournalKind.EDIT,
        expected_generation=expected_generation,
    )


def clear_journal(journal: ImportJournal) -> None:
    """Delete a resolved journal and make that deletion durable."""
    journal.path.unlink(missing_ok=True)
    fsync_directory(journal.path.parent)


def read_journal(path: Path) -> ImportJournal | None:
    """Read one journal file, returning ``None`` when it is unreadable.

    An unreadable journal is not an error to raise at the caller: it is left in
    place and logged, because deleting artifacts on the strength of a payload we
    could not parse is exactly what the re-validation rule exists to prevent.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError, UnicodeDecodeError:
        logger.warning("problem_package: unreadable import journal %s; leaving it in place", path)
        return None
    if not isinstance(payload, dict) or payload.get("version") not in SUPPORTED_JOURNAL_VERSIONS:
        logger.warning("problem_package: unsupported import journal %s; leaving it in place", path)
        return None
    raw_entries = payload.get("entries")
    problem_id = payload.get("problem_id")
    domain = payload.get("domain")
    if not isinstance(raw_entries, list) or not isinstance(problem_id, str) or not isinstance(domain, str):
        logger.warning("problem_package: malformed import journal %s; leaving it in place", path)
        return None
    entries: list[JournalEntry] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        staged, target, root = raw.get("staged"), raw.get("target"), raw.get("root")
        if isinstance(staged, str) and isinstance(target, str) and isinstance(root, str):
            quarantine = raw.get("quarantine")
            entries.append(
                JournalEntry(
                    staged=Path(staged),
                    target=Path(target),
                    root=Path(root),
                    quarantine=Path(quarantine) if isinstance(quarantine, str) else None,
                    target_existed=raw.get("target_existed") is True,
                    removal=raw.get("removal") is True,
                )
            )
    raw_state = payload.get("state")
    try:
        state = PromotionState(raw_state) if isinstance(raw_state, str) else PromotionState("")
    except ValueError:
        logger.warning("problem_package: unknown journal state in %s; leaving it in place", path)
        return None
    raw_kind = payload.get("kind", JournalKind.IMPORT.value)
    try:
        kind = JournalKind(raw_kind) if isinstance(raw_kind, str) else JournalKind("")
    except ValueError:
        logger.warning("problem_package: unknown journal kind in %s; leaving it in place", path)
        return None
    raw_generation = payload.get("expected_generation")
    generation = raw_generation if isinstance(raw_generation, int) and not isinstance(raw_generation, bool) else None
    if kind is JournalKind.EDIT and generation is None:
        logger.warning("problem_package: edit journal %s has no generation fence; leaving it in place", path)
        return None
    return ImportJournal(
        path=path,
        problem_id=problem_id,
        domain=domain,
        entries=tuple(entries),
        state=state,
        kind=kind,
        expected_generation=generation,
    )


async def reconcile_journals(
    journal_root: Path,
    *,
    problem_exists: Callable[[str, str], Awaitable[bool]],
    allowed_roots: frozenset[Path],
    problem_generation: GenerationLookup | None = None,
    edit_guard: EditGuard | None = None,
) -> int:
    """Resolve every stale journal under ``journal_root``.

    Args:
        journal_root: Directory holding the journal files.
        problem_exists: ``(domain, problem_id) -> bool``; whether an **import's**
            transaction committed.
        allowed_roots: The configured storage roots. A journal naming a target
            outside all of them deletes and restores nothing.
        problem_generation: ``(domain, problem_id) -> artifact_generation | None``;
            the fence that tells whether an **edit's** transaction committed.
            When omitted, an edit journal is left in place rather than resolved on
            a guessed signal.
        edit_guard: A scope that owns one problem for the whole of its journal's
            resolution, so a Save cannot start underneath a recovery that already
            decided it was safe. Omitted only where no Save can be concurrent --
            a test, or a caller that has already excluded them.

    Returns:
        The number of journals resolved.
    """
    if not journal_root.is_dir():
        return 0
    resolved = 0
    resolved_roots = {root.resolve() for root in allowed_roots}
    for path in _resolution_order(journal_root):
        # Resolving the newest edit journal may consume the whole chain for that
        # problem while holding one guard. The original directory snapshot still
        # contains the older paths, so skip the entries that chain already cleared.
        if not path.exists():
            continue
        try:
            resolved += await _resolve_one(
                path,
                resolved_roots,
                problem_exists,
                problem_generation,
                edit_guard,
            )
        except Exception:
            # One unresolvable journal must not defer every other one — and at the
            # pre-import call site it would otherwise abort the import itself.
            logger.exception("problem_package: journal %s could not be resolved; leaving it in place", path.name)
    return resolved


def _resolution_order(journal_root: Path) -> list[Path]:
    """Return journals newest-first, which is the only safe restore order.

    Two lost Saves on one problem quarantine in sequence: the first parks the
    author's original, the second parks the first's uncommitted content. Both
    carry the same generation fence, so neither can claim precedence, and
    restoring them oldest-first would leave the *newer* quarantine's content —
    which nothing ever committed — at the target. Undoing newest-first replays
    the displacements in reverse, ending at the true original.

    Two things make that order dependable. Saves on one problem are serialized by
    the ``artifact_generation`` row lock, which a Save takes at its flush before
    journalling, so their journals really are written in sequence rather than
    interleaved. And when a coarse-granularity filesystem reports both journals
    with the same ``mtime``, the tiebreak is the file name — which
    :func:`shared.services.problem_package.staging.new_token` makes monotonic
    precisely so that fallback preserves the same order.
    """
    journals = list(journal_root.glob(f"*{JOURNAL_SUFFIX}"))
    return sorted(journals, key=lambda path: (_mtime(path), path.name), reverse=True)


def _mtime(path: Path) -> float:
    """Return a journal's modification time, or 0 when it cannot be read."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _edit_chain(first: ImportJournal) -> list[ImportJournal]:
    """Return every edit journal for ``first``'s problem, newest-first.

    Two interrupted Saves can leave a chain of quarantines. Restoring only the
    newest and releasing the problem lets a new Save start against the
    intermediate, never-committed files before the older journal restores the
    true predecessor. One guard therefore owns the complete chain.
    """
    chain: list[ImportJournal] = []
    for candidate_path in _resolution_order(first.path.parent):
        candidate = first if candidate_path == first.path else read_journal(candidate_path)
        if candidate is None or candidate.kind is not JournalKind.EDIT:
            continue
        if candidate.domain == first.domain and candidate.problem_id == first.problem_id:
            chain.append(candidate)
    return chain


async def _resolve_one(
    path: Path,
    resolved_roots: set[Path],
    problem_exists: Callable[[str, str], Awaitable[bool]],
    problem_generation: GenerationLookup | None,
    edit_guard: EditGuard | None = None,
) -> int:
    """Resolve a single journal.

    Returns:
        ``1`` when the journal was resolved and cleared, ``0`` when it was left in
        place — unreadable, an edit journal with no generation lookup to resolve
        it against, or one whose problem someone else owns.
    """
    journal = read_journal(path)
    if journal is None:
        return 0
    if journal.kind is JournalKind.EDIT:
        if problem_generation is None:
            logger.warning(
                "problem_package: edit journal %s cannot be resolved without a generation lookup; leaving it in place",
                path.name,
            )
            return 0
        if edit_guard is None:
            await resolve_edit_journal(journal, resolved_roots, problem_generation)
            clear_journal(journal)
            return 1
        # The fence is read, the files are moved, and the journal is cleared all
        # inside the guard: releasing it earlier would let a Save start against
        # a decision already taken.
        async with edit_guard(journal.domain, journal.problem_id) as owned:
            if not owned:
                logger.debug(
                    "problem_package: edit journal %s belongs to a Save still in flight; leaving it in place",
                    path.name,
                )
                return 0
            chain = _edit_chain(journal)
            for chained_journal in chain:
                await resolve_edit_journal(chained_journal, resolved_roots, problem_generation)
            # Keep every recovery record until every filesystem transition in the
            # chain has succeeded. A failure can then retry the complete chain.
            for chained_journal in chain:
                clear_journal(chained_journal)
        return len(chain)
    if await problem_exists(journal.domain, journal.problem_id):
        logger.info("problem_package: import journal %s names a committed problem; clearing it", path.name)
        clear_journal(journal)
        return 1
    removed = delete_orphans(journal, resolved_roots)
    logger.info(
        "problem_package: rolled back orphaned import %s (%s/%s), removed %d artifact(s)",
        path.name,
        journal.domain,
        journal.problem_id,
        removed,
    )
    clear_journal(journal)
    return 1
