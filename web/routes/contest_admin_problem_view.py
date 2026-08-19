#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Build the shared editor view model for one Contest problem-form rendering.

All URLs are resolved here so the shared tab partials never resolve Contest route
names or Contest template paths -- the arrangement
:class:`shared.services.testcase_view.TestCaseRowView` already established for the
per-row table.
"""

from __future__ import annotations

from fastapi import Request

from shared.enumerations import ProblemValidatorType
from shared.services.problem_definition_view import (
    TAB_EDITORIAL,
    TAB_LIMITS,
    TAB_METADATA,
    TAB_STATEMENT,
    ProblemDefinitionView,
    label_for_strategy,
    resolve_tab,
)
from shared.services.problem_editor_header import (
    EditorAction,
    EditorLink,
    EditorNotice,
    ProblemEditorHeaderView,
)

#: Tabs the Contest editor renders. Contest keeps a separate Limits pane, which
#: stays editable while a contest is running.
CONTEST_EDITOR_TABS: tuple[str, ...] = (
    TAB_METADATA,
    TAB_STATEMENT,
    TAB_EDITORIAL,
    TAB_LIMITS,
)


def contest_editor_tabs(validator_type: ProblemValidatorType) -> tuple[str, ...]:
    """Return the panes the Contest definition editor renders.

    The same four for every strategy: what a problem *is* does not depend on how
    it is judged. Strategy-specific material -- the validator, sample interactions
    -- lives in the judgment-data editor, which renders its own pages.

    Args:
        validator_type: The problem's stored strategy, kept in the signature
            because callers pass it and a future pane may need it.

    Returns:
        tuple[str, ...]: Canonical pane values, in display order.
    """
    del validator_type
    return CONTEST_EDITOR_TABS


def build_problem_form_view(
    request: Request,
    *,
    slug: str,
    problem_id: str | None,
    validator_type: ProblemValidatorType,
    active_tab: str | None,
    reselect_uploads: tuple[str, ...] = (),
    problem_label: str = "",
    save_disabled: bool = False,
    solution_test_url: str = "",
    notices: tuple[EditorNotice, ...] = (),
) -> ProblemDefinitionView:
    """Return the view model for the Contest problem editor.

    Args:
        request: The active request.
        slug: The contest login slug.
        problem_id: The problem being edited, or None while creating.
        validator_type: The problem's stored (or chosen) strategy.
        active_tab: Requested tab, from ``?tab=`` or an ``active_tab`` field.
        reselect_uploads: Labels of archives a rejected submission carried, which
            the browser cannot restore and the author must choose again.
        problem_label: How the problem is named beside the heading.
        save_disabled: Whether the contest's state forbids saving right now.
        solution_test_url: Non-scoring solution-test page, for the actors allowed
            to run one; empty otherwise.
        notices: Page-level notices for the shell's notice slot.

    Returns:
        ProblemDefinitionView: The resolved view model.
    """
    is_interactive = validator_type is ProblemValidatorType.INTERACTIVE
    tabs = contest_editor_tabs(validator_type)
    resolved_tab = resolve_tab(active_tab, allowed=frozenset(tabs))
    cancel_url = str(request.url_for("manage_problems", slug=slug))

    if problem_id is None:
        return ProblemDefinitionView(
            validator_type=validator_type,
            is_interactive=is_interactive,
            is_create=True,
            header=ProblemEditorHeaderView(
                title="New problem",
                form_id="edit-form",
                actions=(EditorAction(label="Create problem", icon="check", disabled=save_disabled),),
                back_url=cancel_url,
                strategy_label=label_for_strategy(validator_type),
            ),
            allow_pdf=True,
            tabs=tabs,
            active_tab=resolved_tab,
            save_url=str(request.url_for("new_problem_submit", slug=slug, validator_type=validator_type.value)),
            cancel_url=cancel_url,
            # A problem must exist before it can have judgment data.
            judgment_url="",
            reselect_uploads=reselect_uploads,
            notices=notices,
        )

    statement_url = str(request.url_for("problem_statement", slug=slug, problem_id=problem_id))
    judgment_url = str(request.url_for("problem_judgment_home", slug=slug, problem_id=problem_id))
    links = [EditorLink(label="Judgment data", url=judgment_url, icon="rule")]
    if solution_test_url:
        links.append(EditorLink(label="Test a solution", url=solution_test_url, icon="science"))
    return ProblemDefinitionView(
        validator_type=validator_type,
        is_interactive=is_interactive,
        is_create=False,
        header=ProblemEditorHeaderView(
            title="Edit problem",
            form_id="edit-form",
            subtitle=problem_label,
            actions=(EditorAction(label="Save changes", icon="check", disabled=save_disabled),),
            back_url=cancel_url,
            links=tuple(links),
            strategy_label=label_for_strategy(validator_type),
        ),
        allow_pdf=True,
        tabs=tabs,
        active_tab=resolved_tab,
        save_url=str(request.url_for("edit_problem_submit", slug=slug, problem_id=problem_id)),
        cancel_url=cancel_url,
        editor_base_url=str(request.url_for("edit_problem_form", slug=slug, problem_id=problem_id)),
        judgment_url=judgment_url,
        statement_view_url=statement_url,
        statement_download_url=f"{statement_url}?download=1",
        validator_status_template="admin/problems/_validator_status.html",
        reselect_uploads=reselect_uploads,
        notices=notices,
    )
