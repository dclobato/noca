#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the evidence-gated difficulty presentation."""

from __future__ import annotations

import pytest

from shared.services.arena_difficulty_display import (
    MIN_ATTEMPTS_FOR_DISPLAY,
    DifficultyDisplay,
    difficulty_display,
)


def test_measured_at_threshold() -> None:
    """Exactly MIN_ATTEMPTS_FOR_DISPLAY attempters publishes the stored rating."""
    shown = difficulty_display(70, MIN_ATTEMPTS_FOR_DISPLAY)

    assert shown.state == "measured"
    assert shown.value == 7.0
    assert shown.text == "7.0"
    assert shown.bar_level == 7
    assert shown.description == "Difficulty 7.0 out of 10"
    assert shown.attempted_users == MIN_ATTEMPTS_FOR_DISPLAY


def test_one_below_threshold_is_unknown() -> None:
    """One attempter short of the threshold withholds the number entirely."""
    shown = difficulty_display(50, MIN_ATTEMPTS_FOR_DISPLAY - 1)

    assert shown.state == "unknown"
    assert shown.value is None
    assert shown.text == "—"
    assert shown.bar_level is None
    assert shown.description == "Not enough data yet"


@pytest.mark.parametrize("attempted", [None, 0])
def test_missing_attempts_count_as_zero(attempted: int | None) -> None:
    """A missing attempter count is treated as no evidence."""
    shown = difficulty_display(50, attempted)

    assert shown.state == "unknown"
    assert shown.attempted_users == 0


def test_missing_rating_row_is_unknown_even_with_attempts() -> None:
    """No rating row means nothing to show, regardless of the attempter count."""
    shown = difficulty_display(None, 100)

    assert shown.state == "unknown"
    assert shown.value is None


@pytest.mark.parametrize(("rating", "level"), [(1, 1), (4, 1), (5, 1), (14, 1), (15, 2), (96, 10), (100, 10)])
def test_bar_level_is_clamped_to_the_ten_css_classes(rating: int, level: int) -> None:
    """The bar colour level never falls to 0, which has no CSS class."""
    shown = difficulty_display(rating, MIN_ATTEMPTS_FOR_DISPLAY)

    assert shown.bar_level == level


def test_estimate_fills_the_empty_state_below_the_threshold() -> None:
    """With too few attempters an author estimate is shown, marked as an estimate."""
    shown = difficulty_display(50, MIN_ATTEMPTS_FOR_DISPLAY - 1, expected_difficulty=70)

    assert shown.state == "estimated"
    assert shown.value == 7.0
    assert shown.text == "7.0?"
    assert shown.bar_level == 7
    assert shown.anchor_label == "Challenging"
    assert shown.description == "Estimated difficulty 7.0 out of 10 (Challenging) — not enough submissions yet"


def test_estimate_without_a_rating_row_is_still_shown() -> None:
    """A brand-new problem the worker has not rated yet still shows its estimate."""
    shown = difficulty_display(None, None, expected_difficulty=30)

    assert shown.state == "estimated"
    assert shown.text == "3.0?"
    assert shown.anchor_label == "Easy"


def test_non_anchor_estimate_has_no_anchor_label() -> None:
    """A package can carry any 1-100 value; only exact anchors get a word."""
    shown = difficulty_display(50, 0, expected_difficulty=42)

    assert shown.state == "estimated"
    assert shown.text == "4.2?"
    assert shown.anchor_label is None
    assert shown.description == "Estimated difficulty 4.2 out of 10 — not enough submissions yet"


def test_estimate_never_clears_the_gate_and_is_hidden_once_measured() -> None:
    """At the threshold the measured value is shown and the estimate is ignored entirely."""
    shown = difficulty_display(31, MIN_ATTEMPTS_FOR_DISPLAY, expected_difficulty=70)

    assert shown.state == "measured"
    assert shown.value == 3.1
    assert shown.text == "3.1"
    assert shown.anchor_label is None
    assert "Estimated" not in shown.description


def test_display_is_immutable() -> None:
    """The presentation object is a frozen value."""
    shown = difficulty_display(50, MIN_ATTEMPTS_FOR_DISPLAY)

    with pytest.raises(AttributeError):
        shown.value = 1.0  # type: ignore[misc]
    assert isinstance(shown, DifficultyDisplay)
