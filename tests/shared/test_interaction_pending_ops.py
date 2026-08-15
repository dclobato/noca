#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for retaining and parsing inline sample-interaction rows."""

from __future__ import annotations

import pytest

from shared.services.interaction_pending_ops import (
    SubmittedInteractionError,
    parse_pending_interactions,
    submitted_interaction_rows,
)


def test_submitted_interactions_keep_raw_values_and_indices() -> None:
    rows = submitted_interaction_rows(
        {
            "si_transcript_3": "> 3\n< 5",
            "si_explanation_3": "  retained explanation  ",
        }
    )

    assert len(rows) == 1
    assert rows[0].index == 3
    assert rows[0].transcript_text == "> 3\n< 5"
    assert rows[0].explanation == "  retained explanation  "


def test_a_malformed_interaction_reports_its_submitted_row() -> None:
    with pytest.raises(SubmittedInteractionError) as caught:
        parse_pending_interactions(
            {
                "si_transcript_1": "> valid",
                "si_transcript_7": "missing prefix",
            }
        )

    assert caught.value.index == 7
    assert "Line 1" in str(caught.value)


def test_valid_interactions_keep_their_row_indices() -> None:
    pending = parse_pending_interactions(
        {
            "si_transcript_2": "> 3\n< 5",
            "si_explanation_2": " why ",
        }
    )

    assert len(pending) == 1
    assert pending[0].index == 2
    assert pending[0].explanation == "why"
