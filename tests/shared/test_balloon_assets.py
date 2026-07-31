#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for the framework-agnostic presentation SVG assets."""

from __future__ import annotations

import pytest

from shared.services.balloon_assets import (
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
