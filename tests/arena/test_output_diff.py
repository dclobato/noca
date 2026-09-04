#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for the bounded side-by-side output comparison."""

from __future__ import annotations

import pytest

from arena.services.output_diff import (
    CONTEXT_LINES,
    DEFAULT_EXCERPT_BYTES,
    MAX_DIFF_ROWS,
    MAX_LINE_CHARS,
    build_output_comparison,
)


def _kinds(diff) -> list[str]:  # type: ignore[no-untyped-def]
    return [row.kind for row in diff.rows]


def test_a_single_changed_line_shows_with_its_context() -> None:
    expected = "".join(f"{i}\n" for i in range(1, 8))
    actual = expected.replace("4\n", "44\n")

    diff = build_output_comparison(actual, expected, expected_cut=False)

    assert diff.differing_lines == 1
    assert diff.hidden_differing_lines == 0
    assert _kinds(diff) == ["equal", "changed", "equal"]
    changed = diff.rows[1]
    assert (changed.left_no, changed.left, changed.right_no, changed.right) == (4, "44", 4, "4")
    assert not diff.actual_cut and not diff.expected_cut
    assert not diff.difference_beyond_excerpt and not diff.no_visible_difference


def test_only_the_first_differences_are_shown_and_the_rest_is_counted() -> None:
    expected = "".join(f"line {i}\n" for i in range(1, 30))
    actual = "wrong\n"

    diff = build_output_comparison(actual, expected, expected_cut=False)

    # One changed pair plus 28 expected lines the student never printed.
    assert diff.differing_lines == 29
    assert diff.hidden_differing_lines == 29 - MAX_DIFF_ROWS
    shown = [row for row in diff.rows if row.kind != "equal"]
    assert len(shown) == MAX_DIFF_ROWS
    assert shown[0].kind == "changed"
    assert all(row.kind == "missing" for row in shown[1:])
    rendered = " ".join(row.right or "" for row in diff.rows)
    assert "line 29" not in rendered, "a line past the shown window must never be rendered"


def test_extra_student_lines_are_marked_as_extra() -> None:
    diff = build_output_comparison("1\n2\n3\n", "1\n", expected_cut=False)

    assert _kinds(diff) == ["equal", "extra", "extra"]
    assert diff.rows[1].right is None and diff.rows[1].left == "2"


def test_whitespace_only_differences_are_marked_separately() -> None:
    diff = build_output_comparison("1  2\n", "1 2\n", expected_cut=False)

    assert _kinds(diff) == ["whitespace"]


def test_a_difference_hidden_by_line_splitting_is_stated_not_invented() -> None:
    diff = build_output_comparison("1\n2", "1\n2\n", expected_cut=False)

    assert diff.rows == ()
    assert diff.differing_lines == 0
    assert diff.no_visible_difference
    assert not diff.difference_beyond_excerpt


def test_a_student_excerpt_at_the_judge_cap_is_treated_as_cut() -> None:
    line = "x" * 99 + "\n"
    full_lines = DEFAULT_EXCERPT_BYTES // len(line) + 5
    expected = line * full_lines
    excerpt = expected[:DEFAULT_EXCERPT_BYTES]  # ends mid-line, exactly like the judge's slice

    diff = build_output_comparison(excerpt, expected, expected_cut=False)

    assert diff.actual_cut
    assert diff.differing_lines == 0
    assert diff.difference_beyond_excerpt
    assert not diff.no_visible_difference
    assert diff.rows == ()


def test_a_short_excerpt_without_newline_is_not_mistaken_for_a_cut() -> None:
    diff = build_output_comparison("12", "123\n", expected_cut=False)

    assert not diff.actual_cut
    assert _kinds(diff) == ["changed"]


def test_a_cut_expected_prefix_limits_the_comparison_to_what_was_read() -> None:
    expected_prefix = "1\n2\n3\n4"  # the reader stopped mid-line 4
    actual = "1\n2\n3\n4\n5\n6\n"

    diff = build_output_comparison(actual, expected_prefix, expected_cut=True)

    # Line 4 is partial and dropped; lines 5-6 lie past the read prefix and are not judged.
    assert diff.expected_cut
    assert diff.differing_lines == 0
    assert diff.difference_beyond_excerpt


def test_an_empty_student_output_lists_the_first_expected_lines_as_missing() -> None:
    diff = build_output_comparison(None, "a\nb\n", expected_cut=False)

    assert _kinds(diff) == ["missing", "missing"]
    assert diff.differing_lines == 2


def test_long_lines_are_clipped() -> None:
    diff = build_output_comparison("y" * 1000 + "\n", "z\n", expected_cut=False)

    left = diff.rows[0].left
    assert left is not None
    assert len(left) < 1000
    assert left.startswith("y" * MAX_LINE_CHARS)
    assert left.endswith("[…]")


@pytest.mark.parametrize("context", [CONTEXT_LINES])
def test_context_lines_bracket_each_shown_difference(context: int) -> None:
    expected = "".join(f"{i}\n" for i in range(1, 21))
    actual = expected.replace("5\n", "five\n").replace("15\n", "fifteen\n")

    diff = build_output_comparison(actual, expected, expected_cut=False)

    numbers = [row.right_no for row in diff.rows]
    assert numbers == [5 - context, 5, 5 + context, 15 - context, 15, 15 + context]
