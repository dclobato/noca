#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the backlog index summariser."""

from __future__ import annotations

from scripts.generate_backlog_index import _summarize


def test_leading_heading_is_skipped() -> None:
    """A body opening with a heading summarises its first real paragraph."""
    body = "## Context\n\nArena authentication today is entirely local.\n"

    assert _summarize(body) == "Arena authentication today is entirely local."


def test_later_heading_bounds_the_paragraph() -> None:
    """A heading ends the collected paragraph instead of merging across it."""
    body = "First paragraph.\n## Next section\nSecond paragraph.\n"

    assert _summarize(body) == "First paragraph."


def test_consecutive_leading_headings_are_skipped() -> None:
    """Stacked headings are all skipped before the first paragraph."""
    body = "# Title\n\n### Subtitle\n\nThe real summary.\n"

    assert _summarize(body) == "The real summary."


def test_status_line_is_still_skipped() -> None:
    """The metadata-line behaviour is unchanged by the heading guard."""
    body = "**Status:** Pending\n\n## Context\n\nThe real summary.\n"

    assert _summarize(body) == "The real summary."


def test_body_of_only_headings_summarises_to_nothing() -> None:
    """A body carrying no prose yields an empty summary, not a heading."""
    assert _summarize("## Context\n\n### Detail\n") == ""


def test_plain_paragraph_is_unaffected() -> None:
    """A body with no headings behaves exactly as before."""
    body = "Only paragraph, wrapped\nacross two lines.\n\nSecond paragraph.\n"

    assert _summarize(body) == "Only paragraph, wrapped across two lines."


def test_source_footer_still_stops_collection() -> None:
    """The trailing ``---`` footer remains a hard stop."""
    body = "The real summary.\n\n---\nSource: `docs/BACKLOG.md`.\n"

    assert _summarize(body) == "The real summary."
