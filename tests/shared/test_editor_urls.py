#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Problem-editor return URLs survive a query and an anchor at the same time.

An Arena return URL can carry the problem list's filter state, the tab the author
should land on, and the row they were editing -- all three at once. Naive string
concatenation corrupts exactly that case, which is why these are built by a
helper and pinned here.
"""

from __future__ import annotations

from shared.services.editor_urls import editor_url


def test_a_bare_url_gains_the_tab() -> None:
    """The simple case still produces a plain query string."""
    assert editor_url("/c/x/admin/problems/p1/edit", tab="test-cases") == ("/c/x/admin/problems/p1/edit?tab=test-cases")


def test_an_existing_query_is_preserved_rather_than_replaced() -> None:
    """A second `?` would produce a parameter no server parses."""
    result = editor_url("/admin/problems/p1/edit?page=3&search=graph", tab="metadata")

    assert result == "/admin/problems/p1/edit?page=3&search=graph&tab=metadata"


def test_the_anchor_is_written_last_so_the_tab_is_not_swallowed() -> None:
    """Appending after a fragment would put the parameter inside the fragment."""
    result = editor_url("/admin/problems/p1/edit", tab="test-cases", anchor="tc-42")

    assert result == "/admin/problems/p1/edit?tab=test-cases#tc-42"


def test_query_tab_and_anchor_all_survive_together() -> None:
    """The case that motivated the helper: Arena return state plus tab plus row."""
    result = editor_url(
        "/admin/problems/p1/edit?page=3&search=graph",
        tab="test-cases",
        anchor="tc-42",
    )

    assert result == "/admin/problems/p1/edit?page=3&search=graph&tab=test-cases#tc-42"


def test_an_existing_tab_is_replaced_not_duplicated() -> None:
    """Re-targeting a URL that already names a tab must not send two values."""
    result = editor_url("/admin/problems/p1/edit?tab=metadata&page=2", tab="limits")

    assert result == "/admin/problems/p1/edit?page=2&tab=limits"


def test_an_absolute_url_keeps_its_scheme_and_host() -> None:
    """`url_for` returns absolute URLs, so the helper must not mangle them."""
    result = editor_url("http://testserver/admin/problems/p1/edit", tab="statement")

    assert result == "http://testserver/admin/problems/p1/edit?tab=statement"


def test_omitting_the_tab_leaves_the_query_untouched() -> None:
    """An anchor-only call must not invent a tab parameter."""
    result = editor_url("/admin/problems/p1/edit?page=3", anchor="si-7")

    assert result == "/admin/problems/p1/edit?page=3#si-7"


def test_an_existing_fragment_is_kept_when_no_anchor_is_given() -> None:
    """Adding a tab to an already-anchored URL keeps the anchor."""
    result = editor_url("/admin/problems/p1/edit#tc-9", tab="test-cases")

    assert result == "/admin/problems/p1/edit?tab=test-cases#tc-9"
