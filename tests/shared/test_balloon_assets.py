#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for the framework-agnostic presentation SVG assets."""

from __future__ import annotations

import pytest

from shared.services.balloon_assets import (
    medal_band_for_rank,
    normalize_hex_color,
    normalize_letter,
    render_balloon_svg,
    render_medal_svg,
    render_star_svg,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("#FF0000", "#ff0000"),
        ("00ff00", "#00ff00"),
        ("  #0f0  ", "#00ff00"),
        ("abc", "#aabbcc"),
    ],
)
def test_normalize_hex_color(raw: str, expected: str) -> None:
    assert normalize_hex_color(raw) == expected


@pytest.mark.parametrize("raw", ["zzzzzz", "#12", "12345", "not-a-color", ""])
def test_normalize_hex_color_rejects_invalid(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_hex_color(raw)


def test_normalize_letter_takes_first_uppercased() -> None:
    assert normalize_letter("abc") == "A"
    assert normalize_letter("Z") == "Z"


@pytest.mark.parametrize("raw", ["1", "_", "", "a1"])
def test_normalize_letter_rejects_non_letters(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_letter(raw)


def test_render_embeds_letter_only_when_given() -> None:
    assert "<text" not in render_balloon_svg("#00ff00")
    assert ">A</text>" in render_balloon_svg("#00ff00", "A")
    assert "<text" not in render_star_svg("#00ff00")
    assert ">B</text>" in render_star_svg("#00ff00", "B")


@pytest.mark.parametrize("band", ["gold", "silver", "bronze"])
def test_render_medal_svg_returns_requested_band(band: str) -> None:
    assert render_medal_svg(band).lstrip().startswith("<svg")


def test_render_medal_svg_rejects_unknown_band() -> None:
    with pytest.raises(ValueError, match="Invalid medal band"):
        render_medal_svg("platinum")


def test_letter_contrast_flips_with_fill_luminance() -> None:
    # A light fill needs black text; a dark fill needs white text.
    assert 'fill="#000000"' in render_balloon_svg("#ffffff", "A")
    assert 'fill="#ffffff"' in render_balloon_svg("#000000", "A")


@pytest.mark.parametrize(
    ("rank", "expected"),
    [(1, "gold"), (2, "silver"), (3, "bronze"), (4, None)],
)
def test_medal_band_for_rank_default_cutoffs(rank: int, expected: str | None) -> None:
    assert medal_band_for_rank(rank, gold=1, silver=2, bronze=3) == expected


@pytest.mark.parametrize(
    ("rank", "expected"),
    [(3, "gold"), (4, "silver"), (10, "silver"), (11, "bronze"), (20, "bronze"), (21, None)],
)
def test_medal_band_for_rank_wide_bands(rank: int, expected: str | None) -> None:
    assert medal_band_for_rank(rank, gold=3, silver=10, bronze=20) == expected


def test_medal_band_for_rank_zero_disables_gold() -> None:
    # Ranks that would have been gold fall through to the next enabled band.
    assert medal_band_for_rank(1, gold=0, silver=2, bronze=3) == "silver"
    assert medal_band_for_rank(3, gold=0, silver=2, bronze=3) == "bronze"


def test_medal_band_for_rank_zero_disables_silver() -> None:
    assert medal_band_for_rank(1, gold=1, silver=0, bronze=3) == "gold"
    assert medal_band_for_rank(2, gold=1, silver=0, bronze=3) == "bronze"


@pytest.mark.parametrize("rank", [1, 2, 50])
def test_medal_band_for_rank_all_zero_disables_medals(rank: int) -> None:
    assert medal_band_for_rank(rank, gold=0, silver=0, bronze=0) is None


def test_medal_band_for_rank_equal_cutoffs_favour_the_earlier_band() -> None:
    # gold == silver leaves silver with no positions of its own.
    assert medal_band_for_rank(2, gold=2, silver=2, bronze=5) == "gold"
    assert medal_band_for_rank(3, gold=2, silver=2, bronze=5) == "bronze"


@pytest.mark.parametrize("rank", [0, -1])
def test_medal_band_for_rank_rejects_non_positions(rank: int) -> None:
    assert medal_band_for_rank(rank, gold=1, silver=2, bronze=3) is None
