#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Rules for the breadcrumb bar.

Its horizontal padding is the same on every page: four templates could once
override it, so the bar sat at Bootstrap's 12px gutter on Scoreboard,
Clarifications and one submission page and at the 24px `px-4` default
everywhere else, and moving between Score and Runs visibly stepped it.

A page also only earns a breadcrumb when it has somewhere to go back to that
the contest navigation band does not already show. The band marks the current
section and links to the dashboard, so a two-item "Contest Dashboard > Runs"
trail is a whole bar of chrome repeating what is directly above it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / "web" / "template"
_BASE = _TEMPLATE_ROOT / "_base.html"


def test_the_bar_declares_one_fixed_padding() -> None:
    assert '<div class="breadcrumb-bar container-fluid px-4 py-2"' in _BASE.read_text(encoding="utf-8")


def test_no_page_overrides_the_bar_padding() -> None:
    """A page needing different padding aligns its content to the bar, not the bar to it."""
    offenders = [
        path.relative_to(_TEMPLATE_ROOT).as_posix()
        for path in _TEMPLATE_ROOT.rglob("*.html")
        if path != _BASE and "breadcrumb_container_class" in path.read_text(encoding="utf-8")
    ]

    assert offenders == [], f"templates overriding the breadcrumb bar padding: {offenders}"


# Top-level contest sections: each is an entry in the navigation band, so a
# breadcrumb here could only repeat it.
_SECTION_PAGES = [
    "contest/clarifications.html",
    "contest/problems_list.html",
    "contest/runs.html",
    "contest/scoreboard.html",
    "contest/solution_tests.html",
    "contest/tasks.html",
]


@pytest.mark.parametrize("page", _SECTION_PAGES)
def test_a_top_level_section_has_no_breadcrumb(page: str) -> None:
    assert "breadcrumb" not in (_TEMPLATE_ROOT / page).read_text(encoding="utf-8")


def test_pages_with_a_real_second_level_keep_theirs() -> None:
    """The bar is not gone: it still serves pages that are genuinely nested."""
    deeper = [
        "contest/problem_detail.html",
        "submissions/submission_review.html",
        "admin/problems/list.html",
    ]

    for page in deeper:
        assert "{% block breadcrumb %}" in (_TEMPLATE_ROOT / page).read_text(encoding="utf-8")
