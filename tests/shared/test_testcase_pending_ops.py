#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the one form parser the judgment-data pages still need.

Everything on those pages posts immediately, so the only thing left to parse from
a form is what the author typed inline and has not saved: the rows the page's own
Save applies. The Save-wide parser that once turned a whole form -- archives,
removals, toggles, a validator action -- into one operation set is gone with the
single Save that needed it, and so are its conflict rules: with one action per
request there is nothing to combine.

Where each of its refusals lives now: an archive is parsed by the route that
receives it (``tests/web/test_contest_admin_problem_judgment.py`` and the Arena
twin), a strategy this build cannot judge is refused by
``shared.services.problem_save_errors``, an action naming a row that no longer
exists is refused under the row lock by
``shared.services.judgment_case_action``, and a validator on a standard problem is
refused by the validator routes.
"""

from __future__ import annotations

from shared.services.testcase_pending_ops import (
    first_oversized_submitted_case,
    parse_inline_added_cases,
    submitted_case_rows,
)
from shared.tc_zip import MAX_INLINE_TESTCASE_BYTES


def test_inline_rows_are_parsed_in_order() -> None:
    rows = parse_inline_added_cases(
        {"tc_in_0": "a", "tc_out_0": "A", "tc_in_1": "b", "tc_out_1": "B", "tc_is_sample_1": "true"},
        interactive=False,
    )

    assert [case.input_bytes for case in rows] == [b"a", b"b"]
    assert [case.output_bytes for case in rows] == [b"A", b"B"]
    assert [case.is_sample for case in rows] == [False, True]


def test_interactive_inline_rows_carry_no_output_and_are_never_samples() -> None:
    """An interactive problem's cases are input-only and secret, whatever was posted."""
    rows = parse_inline_added_cases(
        {"tc_in_0": "a", "tc_out_0": "ignored", "tc_is_sample_0": "true"},
        interactive=True,
    )

    assert rows[0].output_bytes is None
    assert rows[0].is_sample is False


def test_empty_inline_rows_are_dropped() -> None:
    """An author who adds a row and leaves it blank has added nothing."""
    assert parse_inline_added_cases({"tc_in_0": "", "tc_out_0": ""}, interactive=False) == ()


def test_a_row_with_only_an_output_still_counts_as_typed() -> None:
    """It is invalid, but silently discarding what the author typed is worse."""
    rows = parse_inline_added_cases({"tc_in_0": "", "tc_out_0": "A"}, interactive=False)

    assert len(rows) == 1
    assert rows[0].input_bytes == b""


def test_an_explanation_rides_with_its_row() -> None:
    rows = parse_inline_added_cases({"tc_in_0": "a", "tc_out_0": "A", "tc_explanation_0": "  why  "}, interactive=False)

    assert rows[0].explanation == "why"


def test_submitted_rows_keep_raw_values_and_sparse_indices_for_a_rejection() -> None:
    rows = submitted_case_rows(
        {
            "tc_in_2": "  input  ",
            "tc_out_2": "  output  ",
            "tc_explanation_2": "  explanation  ",
            "tc_is_sample_2": "true",
        }
    )

    assert len(rows) == 1
    assert rows[0].index == 2
    assert rows[0].input_text == "  input  "
    assert rows[0].output_text == "  output  "
    assert rows[0].explanation == "  explanation  "
    assert rows[0].is_sample is True


def test_oversized_submitted_rows_identify_the_exact_side() -> None:
    rows = submitted_case_rows(
        {
            "tc_in_0": "small",
            "tc_out_0": "small",
            "tc_in_4": "small",
            "tc_out_4": "x" * (MAX_INLINE_TESTCASE_BYTES + 1),
        }
    )

    assert first_oversized_submitted_case(rows, interactive=False) == (4, "output")
