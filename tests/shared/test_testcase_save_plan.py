#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Save's test-case plan and its staged materialization.

The planner is pure, so most of this is arithmetic about ordinals. The
materializer is checked against a real directory because the property that
matters -- a Save that reorders cases must not overwrite a file it still needs --
is a property of renames, not of the plan.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.services.testcase_pending_ops import CaseContent, PendingTestCaseOps
from shared.services.testcase_save_plan import (
    CaseOrigin,
    CurrentCase,
    MissingCarriedCase,
    build_desired_cases,
    materialize,
)


def _current() -> list[CurrentCase]:
    """Three cases, the middle one a sample."""
    return [
        CurrentCase(id="a", ordinal=1, is_sample=False),
        CurrentCase(id="b", ordinal=2, is_sample=True),
        CurrentCase(id="c", ordinal=3, is_sample=False),
    ]


def _seed(directory: Path) -> None:
    """Write the three current cases into ``directory``."""
    for ordinal, letter in ((1, "a"), (2, "b"), (3, "c")):
        (directory / f"{ordinal:03d}.in").write_bytes(f"{letter}-in\n".encode())
        (directory / f"{ordinal:03d}.out").write_bytes(f"{letter}-out\n".encode())


def test_removal_renumbers_the_survivors() -> None:
    desired = build_desired_cases(_current(), PendingTestCaseOps(removals=frozenset({"b"})), interactive=False)
    assert [(case.source_id, case.ordinal, case.source_ordinal) for case in desired] == [
        ("a", 1, 1),
        ("c", 2, 3),
    ]


def test_additions_are_appended_after_the_survivors() -> None:
    ops = PendingTestCaseOps(
        removals=frozenset({"a"}),
        added=(CaseContent(input_bytes=b"n", output_bytes=b"N"),),
    )
    desired = build_desired_cases(_current(), ops, interactive=False)
    assert [case.origin for case in desired] == [CaseOrigin.CARRIED, CaseOrigin.CARRIED, CaseOrigin.ADDED]
    assert desired[-1].ordinal == 3


def test_sample_toggle_flips_only_the_named_row() -> None:
    ops = PendingTestCaseOps(sample_toggles=frozenset({"a", "b"}))
    desired = build_desired_cases(_current(), ops, interactive=False)
    assert [case.is_sample for case in desired] == [True, False, False]


def test_interactive_forces_every_case_secret() -> None:
    desired = build_desired_cases(_current(), PendingTestCaseOps(), interactive=True)
    assert [case.is_sample for case in desired] == [False, False, False]


def test_replacement_keeps_the_row_and_its_position() -> None:
    ops = PendingTestCaseOps(replacements={"b": CaseContent(input_bytes=b"x", output_bytes=b"X")})
    desired = build_desired_cases(_current(), ops, interactive=False)
    assert desired[1].origin is CaseOrigin.REPLACED
    assert desired[1].source_id == "b"
    assert desired[1].set_explanation is False


def test_replacement_explanation_only_overrides_when_the_archive_carried_one() -> None:
    ops = PendingTestCaseOps(replacements={"b": CaseContent(input_bytes=b"x", output_bytes=b"X", explanation="why")})
    desired = build_desired_cases(_current(), ops, interactive=False)
    assert desired[1].set_explanation is True
    assert desired[1].explanation == "why"


def test_a_zip_replacement_keeps_the_rows_sample_flag() -> None:
    """An archive carries content only: it has no sample flag to state."""
    ops = PendingTestCaseOps(replacements={"b": CaseContent(input_bytes=b"x", output_bytes=b"X", is_sample=False)})

    desired = build_desired_cases(_current(), ops, interactive=False)

    assert desired[1].is_sample is True


def test_an_inline_edit_states_the_sample_flag_it_submitted() -> None:
    """The form shows the checkbox, so leaving it unchecked is a decision.

    Preserving the stored flag here silently discarded what the author submitted:
    the case they made secret came back a sample.
    """
    ops = PendingTestCaseOps(
        replacements={"b": CaseContent(input_bytes=b"x", output_bytes=b"X", is_sample=False, states_metadata=True)}
    )

    desired = build_desired_cases(_current(), ops, interactive=False)

    assert desired[1].is_sample is False


def test_an_inline_edit_can_clear_the_explanation() -> None:
    """Emptying the box is how an explanation is removed, and it must reach the row."""
    ops = PendingTestCaseOps(
        replacements={"b": CaseContent(input_bytes=b"x", output_bytes=b"X", explanation=None, states_metadata=True)}
    )

    desired = build_desired_cases(_current(), ops, interactive=False)

    assert desired[1].set_explanation is True
    assert desired[1].explanation is None


def test_an_inline_edit_cannot_make_an_interactive_case_a_sample() -> None:
    """Interactive problems have no public cases; the form's flag cannot change that."""
    ops = PendingTestCaseOps(
        replacements={"b": CaseContent(input_bytes=b"x", output_bytes=None, is_sample=True, states_metadata=True)}
    )

    desired = build_desired_cases(_current(), ops, interactive=True)

    assert desired[1].is_sample is False


def test_bulk_replacement_discards_the_current_list() -> None:
    ops = PendingTestCaseOps(bulk_cases=(CaseContent(input_bytes=b"1", output_bytes=b"2"),))
    desired = build_desired_cases(_current(), ops, interactive=False)
    assert len(desired) == 1
    assert desired[0].source_id is None


def test_an_empty_bulk_archive_is_a_deliberate_empty_set() -> None:
    desired = build_desired_cases(_current(), PendingTestCaseOps(bulk_cases=()), interactive=False)
    assert desired == []


def test_materialize_renumbers_without_clobbering(tmp_path: Path) -> None:
    _seed(tmp_path)
    ops = PendingTestCaseOps(removals=frozenset({"a"}))
    desired = build_desired_cases(_current(), ops, interactive=False)

    result = materialize(desired, tmp_path)

    assert (tmp_path / "001.in").read_bytes() == b"b-in\n"
    assert (tmp_path / "002.in").read_bytes() == b"c-in\n"
    assert not (tmp_path / "003.in").exists()
    assert [case.input_size_bytes for case in result] == [5, 5]


def test_materialize_writes_replacements_and_additions(tmp_path: Path) -> None:
    _seed(tmp_path)
    ops = PendingTestCaseOps(
        replacements={"b": CaseContent(input_bytes=b"new-in\n", output_bytes=b"new-out\n")},
        added=(CaseContent(input_bytes=b"added\n", output_bytes=b"ADDED\n"),),
    )
    desired = build_desired_cases(_current(), ops, interactive=False)

    materialize(desired, tmp_path)

    assert (tmp_path / "002.in").read_bytes() == b"new-in\n"
    assert (tmp_path / "004.in").read_bytes() == b"added\n"


def test_materialize_drops_expected_output_for_an_interactive_problem(tmp_path: Path) -> None:
    _seed(tmp_path)
    desired = build_desired_cases(_current(), PendingTestCaseOps(), interactive=True)

    result = materialize(desired, tmp_path, interactive=True)

    assert not (tmp_path / "001.out").exists()
    assert all(case.output_size_bytes is None for case in result)


def test_materialize_leaves_no_temporary_files_behind(tmp_path: Path) -> None:
    _seed(tmp_path)
    desired = build_desired_cases(_current(), PendingTestCaseOps(bulk_cases=()), interactive=False)

    materialize(desired, tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_a_carried_case_with_no_input_file_aborts(tmp_path: Path) -> None:
    """Storage corruption must not be laundered into an empty test case.

    The action was about some *other* case; carrying this one is incidental, so
    silently materializing it as empty commits a zero-byte case the author never
    wrote and hides the damage. Replacing that case is still the way out, because
    a replacement brings content and carries nothing.
    """
    _seed(tmp_path)
    (tmp_path / "002.in").unlink()
    desired = build_desired_cases(_current(), PendingTestCaseOps(removals=frozenset({"c"})), interactive=False)

    with pytest.raises(MissingCarriedCase, match="no input file"):
        materialize(desired, tmp_path, interactive=False)


def test_a_carried_standard_case_with_no_output_file_aborts(tmp_path: Path) -> None:
    """A missing expected output is storage corruption for a standard case."""
    _seed(tmp_path)
    (tmp_path / "002.out").unlink()
    desired = build_desired_cases(_current(), PendingTestCaseOps(removals=frozenset({"c"})), interactive=False)

    with pytest.raises(MissingCarriedCase, match="no expected-output file"):
        materialize(desired, tmp_path, interactive=False)


def test_an_interactive_case_may_be_carried_without_an_output_file(tmp_path: Path) -> None:
    """Interactive cases intentionally contain only input."""
    _seed(tmp_path)
    (tmp_path / "002.out").unlink()
    desired = build_desired_cases(_current(), PendingTestCaseOps(removals=frozenset({"c"})), interactive=True)

    result = materialize(desired, tmp_path, interactive=True)

    assert [case.output_size_bytes for case in result] == [None, None]


def test_replacing_the_damaged_case_still_works(tmp_path: Path) -> None:
    """The documented recovery path must not be blocked by the guard."""
    _seed(tmp_path)
    (tmp_path / "002.in").unlink()
    ops = PendingTestCaseOps(replacements={"b": CaseContent(input_bytes=b"fixed\n", output_bytes=b"ok\n")})

    materialize(build_desired_cases(_current(), ops, interactive=False), tmp_path, interactive=False)

    assert (tmp_path / "002.in").read_bytes() == b"fixed\n"
