#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Presentation model for the problem *definition* editor, shared by both modules.

A problem is edited through two doors. This is the first: what the problem *is* --
title, statement, editorial, illustration, categories, and (Contest only) resource limits --
saved by one form with one Save. What judging runs against lives behind the
second door, :mod:`shared.services.judgment_page_view`, on pages of its own.

The panes here are client-side: they are a handful of fields that belong to one
transaction, so switching between them must not lose typed input and does not need
a round trip. Judgment data is the opposite on both counts, which is why it is not
here.

Both modules render the same statement and editorial panes, so they must stay free of
module-specific ``url_for`` route names: each module builds one
:class:`ProblemDefinitionView` whose URLs are already resolved -- the arrangement
:class:`shared.services.testcase_view.TestCaseRowView` uses for the row table.

The validator status badge is the one piece that cannot be reduced to a URL: each
module owns a differently-shaped partial at a different path, so the view carries
the template name and the shared pane includes it indirectly.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.enumerations import ProblemValidatorType
from shared.services.editor_urls import editor_url

#: Canonical definition panes, in display order.
TAB_METADATA = "metadata"
TAB_STATEMENT = "statement"
TAB_EDITORIAL = "editorial"
TAB_LIMITS = "limits"

#: Every canonical value, in display order.
ALL_TABS: tuple[str, ...] = (TAB_METADATA, TAB_STATEMENT, TAB_EDITORIAL, TAB_LIMITS)

#: Web's pre-split vocabulary, kept working so old links still land on a rendered
#: pane. ``test-cases`` and ``sample-interactions`` are *not* aliases: they name pages
#: that now live elsewhere, and the editor redirects them there instead.
LEGACY_TAB_ALIASES: dict[str, str] = {"content": TAB_METADATA}

#: Requested panes that moved to the judgment-data editor, mapped to its page keys.
MOVED_TO_JUDGMENT: dict[str, str] = {
    "test-cases": "test-cases",
    "sample-interactions": "interactions",
}


def resolve_tab(raw: str | None, *, allowed: frozenset[str]) -> str:
    """Return the pane to activate, falling back rather than failing.

    A pane is a view preference, never an authorization or correctness decision,
    so an unknown or retired value quietly resolves to Metadata instead of
    erroring: the alternative is a 404 on a page the author is entitled to see.

    Args:
        raw: The requested value, from ``?tab=`` or an ``active_tab`` field.
        allowed: The panes this editor actually renders.

    Returns:
        str: A canonical value that is guaranteed to be rendered.
    """
    if raw is None:
        return TAB_METADATA
    candidate = LEGACY_TAB_ALIASES.get(raw, raw)
    if candidate not in allowed:
        return TAB_METADATA
    return candidate


@dataclass(frozen=True)
class ProblemDefinitionView:
    """Pre-resolved URLs and flags for one rendering of the definition editor.

    Attributes:
        validator_type: The problem's stored (or, when creating, chosen) strategy.
        is_interactive: Whether that strategy is ``INTERACTIVE``. Derived once
            here so no template re-derives it and disagrees.
        is_create: True while creating.
        allow_pdf: Whether the statement pane offers PDF upload and preview
            (Contest keeps PDF-or-Markdown; Arena stays Markdown-only).
        tabs: The panes this editor renders, in display order.
        active_tab: The pane to activate on load.
        save_url: Target of the single Save form.
        cancel_url: Where Cancel/Back returns to.
        judgment_url: The other door -- test cases, validator, interactions --
            or empty while creating, when the problem does not exist yet.
        editor_base_url: Editor URL used to build ``?tab=`` links, empty while
            creating.
        statement_view_url: Contest PDF statement URL, or None.
        statement_download_url: Contest PDF download URL, or None.
        validator_status_template: Module-relative validator status partial, for
            the read-only badge the Metadata pane shows.
        strategy_label: Human-readable strategy name for the read-only badge.
        reselect_uploads: Labels of uploads a rejected submission carried, which
            the browser cannot restore and the author must choose again.
    """

    validator_type: ProblemValidatorType
    is_interactive: bool
    is_create: bool
    allow_pdf: bool
    tabs: tuple[str, ...]
    active_tab: str
    save_url: str
    cancel_url: str
    judgment_url: str = ""
    editor_base_url: str = ""
    statement_view_url: str | None = None
    statement_download_url: str | None = None
    validator_status_template: str | None = None
    strategy_label: str = ""
    reselect_uploads: tuple[str, ...] = ()

    def tab_url(self, tab: str) -> str:
        """Return the editor URL that opens ``tab`` directly.

        Args:
            tab: A canonical pane value.

        Returns:
            str: The editor URL carrying that pane, or an empty string while
            creating, when there is no editor URL to link to yet.
        """
        if not self.editor_base_url:
            return ""
        return editor_url(self.editor_base_url, tab=tab)


def label_for_strategy(validator_type: ProblemValidatorType) -> str:
    """Return the display label for a stored strategy.

    Args:
        validator_type: The problem's stored strategy.

    Returns:
        str: A short human-readable name for the read-only editor badge.
    """
    labels = {
        ProblemValidatorType.STANDARD: "Standard",
        ProblemValidatorType.INTERACTIVE: "Interactive",
        ProblemValidatorType.OUTPUT_CHECKER: "Output checker",
    }
    return labels[validator_type]
