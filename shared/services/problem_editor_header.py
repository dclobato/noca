#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Presentation model for the chrome both problem editors share.

A problem is edited through two doors -- the *definition* editor
(:mod:`shared.services.problem_definition_view`) and the *judgment-data* editor
(:mod:`shared.services.judgment_page_view`) -- and an author moves between them
constantly. They used to draw their own headers, so switching doors moved the
title, resized the buttons and shifted the page margins. This module is the one
description of that chrome, so both doors render the same title row and the same
action bar and only their contents differ.

The bar holds three groups, in this order: the submitters that change publication
state, the Back link, and -- pushed to the trailing edge -- the cross-link to the
other door, any module extras, and the read-only strategy badge.

An action is a submitter rather than a link because both doors post: the
definition editor submits the detached ``#edit-form`` that its panes attach to,
while the judgment editor has no such form and the header renders its own,
pointed at ``state_form_url``. ``form_id`` names whichever one applies, so the
partial does not care which door it is drawing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EditorAction:
    """One submit button in the editor action bar.

    Attributes:
        label: What the author reads.
        icon: Material icon name shown before the label.
        variant: Bootstrap button variant class.
        value: Value posted as ``save_action``, or empty for a plain submit.
        disabled: Whether the button is offered but not usable.
    """

    label: str
    icon: str
    variant: str = "btn-primary"
    value: str = ""
    disabled: bool = False


@dataclass(frozen=True)
class EditorLink:
    """One link in the action bar's trailing group.

    Attributes:
        label: What the author reads, and the accessible name when the link
            renders as an icon alone.
        url: Where it goes.
        icon: Material icon name, or empty for a text-only link.
        icon_only: Render the icon alone, with ``label`` as its title and
            accessible name.
    """

    label: str
    url: str
    icon: str = ""
    icon_only: bool = False


@dataclass(frozen=True)
class EditorNotice:
    """One page-level notice shown in the editor's notice slot.

    Both doors put their notices in the same place -- under the action bar,
    above the pane strip -- so a notice cannot push the title and buttons of one
    door to a different height than the other's.

    Attributes:
        text: What the author reads.
        variant: Bootstrap alert variant class.
        icon: Material icon name, or empty for a text-only alert.
    """

    text: str
    variant: str = "alert-warning"
    icon: str = ""


@dataclass(frozen=True)
class ProblemEditorHeaderView:
    """The title row and action bar for one rendering of either editor door.

    Attributes:
        title: The page's heading.
        form_id: The form every action submits. On the definition editor this is
            the detached ``#edit-form``; on the judgment editor the header
            renders that form itself, from ``state_form_url``.
        subtitle: Quiet text beside the heading -- which problem this is.
        icon: Material icon shown before the heading.
        actions: Publication-state submitters, in display order.
        state_form_url: Post target for the form the header renders itself. Empty
            when the actions submit a form the page already owns.
        state_form_return_url: Safe local path that form returns to.
        back_url: Where Back goes, or empty to omit it.
        back_label: What Back reads.
        links: Trailing group -- the other door, module extras, downloads.
        strategy_label: Read-only validation strategy, or empty to omit the
            badge. A strategy is chosen once and never changes, so it is never
            an input.
    """

    title: str
    form_id: str
    subtitle: str = ""
    icon: str = "assignment"
    actions: tuple[EditorAction, ...] = ()
    state_form_url: str = ""
    state_form_return_url: str = ""
    back_url: str = ""
    back_label: str = "Back"
    links: tuple[EditorLink, ...] = ()
    strategy_label: str = ""


def publish_state_actions(*, disabled: bool = False) -> tuple[EditorAction, ...]:
    """Return the paired publish-state submitters, worded once for both doors.

    Arena offers the same choice from either door, so the labels live here rather
    than in two templates that could drift apart.

    Args:
        disabled: Whether both submitters are shown unusable.

    Returns:
        tuple[EditorAction, ...]: The enable and disable submitters, in order.
    """
    return (
        EditorAction(
            label="Save and enable",
            icon="visibility",
            variant="btn-primary",
            value="enable",
            disabled=disabled,
        ),
        EditorAction(
            label="Save and disable",
            icon="visibility_off",
            variant="btn-outline-danger",
            value="disable",
            disabled=disabled,
        ),
    )


def arena_problem_editor_actions(*, disabled: bool = False) -> tuple[EditorAction, ...]:
    """Return the submitters for the Arena problem definition editor.

    Places 'Save and keep editing' first so an author can save incremental work
    without leaving the page, followed by the publish-state submitters ('Save and
    enable', 'Save and disable').

    Args:
        disabled: Whether the submitters are shown unusable.

    Returns:
        tuple[EditorAction, ...]: The definition editor submitters in display order.
    """
    return (
        EditorAction(
            label="Save and keep editing",
            icon="save",
            variant="btn-secondary",
            value="keep_editing",
            disabled=disabled,
        ),
        EditorAction(
            label="Save and enable",
            icon="visibility",
            variant="btn-primary",
            value="enable",
            disabled=disabled,
        ),
        EditorAction(
            label="Save and disable",
            icon="visibility_off",
            variant="btn-outline-danger",
            value="disable",
            disabled=disabled,
        ),
    )
