#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Pane resolution never fails a page the author is entitled to see.

A pane is a view preference, not an authorization or correctness decision, so an
unknown or retired value falls back to Metadata rather than erroring. These pin
that, plus the per-module pane sets: Arena keeps its resource limits inside
Metadata and renders no Limits pane, so it must not honour ``?tab=limits``.

Test cases and sample interactions are deliberately absent: they are pages of the
judgment-data editor now, and the editor redirects those values there instead of
resolving them to a pane.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from shared.enumerations import ProblemValidatorType
from shared.services.problem_definition_view import (
    ALL_TABS,
    MOVED_TO_JUDGMENT,
    TAB_EDITORIAL,
    TAB_LIMITS,
    TAB_METADATA,
    TAB_STATEMENT,
    label_for_strategy,
    resolve_tab,
)

_ALL = frozenset(ALL_TABS)


@pytest.mark.parametrize("tab", ALL_TABS)
def test_every_canonical_pane_resolves_to_itself(tab: str) -> None:
    """Contest renders all three panes, so each value survives."""
    assert resolve_tab(tab, allowed=_ALL) == tab


def test_an_unknown_pane_falls_back_instead_of_failing() -> None:
    """The alternative would be a 404 on a page the author may see."""
    assert resolve_tab("bogus", allowed=_ALL) == TAB_METADATA


def test_no_pane_at_all_opens_metadata() -> None:
    """The plain editor URL opens where an author starts reading."""
    assert resolve_tab(None, allowed=_ALL) == TAB_METADATA


def test_the_retired_content_value_still_lands_on_a_rendered_pane() -> None:
    """Old bookmarks and in-flight pages named `content`; they must not break."""
    assert resolve_tab("content", allowed=_ALL) == TAB_METADATA


def test_a_pane_the_module_does_not_render_falls_back() -> None:
    """Arena has no Limits pane, so `?tab=limits` must not select a missing pane."""
    arena_tabs = frozenset({TAB_METADATA, TAB_STATEMENT, TAB_EDITORIAL})

    assert resolve_tab(TAB_LIMITS, allowed=arena_tabs) == TAB_METADATA


@pytest.mark.parametrize("moved", sorted(MOVED_TO_JUDGMENT))
def test_a_pane_that_moved_is_not_silently_resolved_here(moved: str) -> None:
    """The editor redirects these to the judgment editor, so resolution must not
    quietly turn them into Metadata behind its back."""
    assert moved not in ALL_TABS
    assert MOVED_TO_JUDGMENT[moved] in {"test-cases", "validator", "interactions"}


@pytest.mark.parametrize(
    ("strategy", "expected"),
    [
        (ProblemValidatorType.STANDARD, "Standard"),
        (ProblemValidatorType.INTERACTIVE, "Interactive"),
        (ProblemValidatorType.OUTPUT_CHECKER, "Output checker"),
    ],
)
def test_every_strategy_has_a_badge_label(strategy: ProblemValidatorType, expected: str) -> None:
    """Both editors name the strategy, including the reserved one."""
    assert label_for_strategy(strategy) == expected


def test_shared_partials_resolve_no_module_route_names() -> None:
    """The whole point of the view models: shared markup stays module-neutral.

    A `url_for` in a shared partial would resolve a route name that exists in only
    one module, so the other module's editor would fail to render. The judgment
    partials are held to the same rule as the definition ones.
    """
    root = Path(__file__).resolve().parents[2] / "shared" / "template" / "_partials"
    shared_panes = (
        "problem_editor_shell.html",
        "problem_statement_tab.html",
        "validator_choice_cards.html",
        "testcase_list_table.html",
        "sample_interaction_list_table.html",
        "judgment_shell.html",
        "judgment_testcases_page.html",
        "judgment_validator_page.html",
        "judgment_interactions_page.html",
    )

    for name in shared_panes:
        # Strip the Jinja comment blocks first: they explain the rule and name it.
        markup = re.sub(r"\{#.*?#\}", "", (root / name).read_text(encoding="utf-8"), flags=re.S)
        assert "url_for" not in markup, name
