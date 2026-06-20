#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Arena category upsert script parser."""

import random

import pytest

from scripts.arena.upsert_arena_categories import load_categories, parse_category_line


def test_parse_category_line_with_explicit_color() -> None:
    parsed = parse_category_line(
        "GCD / LCM #ABCDEF",
        line_number=1,
        rng=random.Random(1),
    )

    assert parsed is not None
    assert parsed.name == "GCD / LCM"
    assert parsed.color == "#abcdef"


def test_parse_category_line_assigns_random_color() -> None:
    parsed = parse_category_line(
        "Number theory",
        line_number=1,
        rng=random.Random(1),
    )

    assert parsed is not None
    assert parsed.name == "Number theory"
    assert parsed.color == "#44cb63"


def test_parse_category_line_rejects_invalid_color() -> None:
    with pytest.raises(ValueError, match="Line 7"):
        parse_category_line("Modular arithmetic #92911", line_number=7, rng=random.Random(1))


def test_load_categories_preserves_names_with_slashes(tmp_path) -> None:
    path = tmp_path / "categories-en.txt"
    path.write_text("Arithmetic #00ff00\nNumber theory\nGCD / LCM\n", encoding="utf-8")

    categories = load_categories(path, seed=1)

    assert [category.name for category in categories] == ["Arithmetic", "Number theory", "GCD / LCM"]
    assert categories[0].color == "#00ff00"
    assert categories[1].color == "#44cb63"
