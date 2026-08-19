#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The one place the Contest problem editor's template context is built.

The editor is rendered from four places -- the GET, a limits-only rejection, a
validation rejection, and the Save's own error paths -- and they used to build
this dictionary three times over. That is how the inline test-case rows came to
be dropped on a failed Save: only one of the copies would ever have been taught to
echo them back, and none was.

So the context is assembled once, and echoing a rejected submission is part of
its contract rather than something each caller remembers.
"""

from __future__ import annotations

from typing import Any

import anyio
from fastapi import Request

from shared.enumerations import ProblemValidatorType, RoleEnum
from shared.services.problem_editor_header import EditorNotice
from web.config import settings
from web.dependencies import ContestAdminContext
from web.models.contest import Contest
from web.models.problem import Problem
from web.routes.contest_admin_problem_helpers import (
    _is_edit_allowed,
    _is_limits_edit_allowed,
    _is_remove_allowed,
    _label,
    _remove_blocked_reason,
)
from web.routes.contest_admin_problem_limits_helpers import _build_profiling_limits_context
from web.routes.contest_admin_problem_view import build_problem_form_view
from web.services.problem_service import (
    BALLOON_COLORS,
    get_md_statement_path,
    get_statement_path,
)


def _solution_test_url(request: Request, ctx: ContestAdminContext, problem: Problem) -> str:
    """Return the non-scoring solution-test page, for the actors allowed to run one.

    Args:
        request: The active request.
        ctx: The resolved contest-admin context.
        problem: The problem being edited.

    Returns:
        str: The URL, or an empty string when this actor may not run a test.
    """
    allowed = {RoleEnum.ADMIN.value, RoleEnum.UBERADMIN.value, RoleEnum.JUDGE.value}
    if ctx.actor.role not in allowed:
        return ""
    page = request.url_for("contest_solution_tests", slug=ctx.contest.login_slug)
    return f"{page}?problem_id={problem.id}"


def editor_notices(contest: Contest, *, has_problem: bool) -> tuple[EditorNotice, ...]:
    """Return the contest-state notices the editor's notice slot shows.

    Args:
        contest: The contest the problem belongs to.
        has_problem: False while creating, when removal cannot be blocked yet.

    Returns:
        tuple[EditorNotice, ...]: Notices in display order, possibly empty.
    """
    notices: list[EditorNotice] = []
    if not _is_edit_allowed(contest):
        if contest.is_running:
            reason = (
                "Contest is running — only the Limits tab may be updated. Problem content and test cases stay locked."
            )
        elif contest.is_past:
            reason = "Contest has ended — this problem cannot be edited."
        elif not contest.active:
            reason = "Contest is inactive — this problem cannot be edited."
        else:
            reason = "Problem editing is not allowed at this time."
        notices.append(EditorNotice(text=reason, variant="alert-warning", icon="lock"))
    elif has_problem and not _is_remove_allowed(contest):
        notices.append(
            EditorNotice(
                text="Note: this problem cannot be removed while the contest is running.",
                variant="alert-info",
                icon="info",
            )
        )
    return tuple(notices)


def stored_form_data(problem: Problem) -> dict[str, Any]:
    """Return the scalar form values as the problem currently stores them."""
    return {
        "title": problem.title,
        "color": problem.color or "#000000",
        "author": problem.author or "",
        "notes": problem.notes or "",
        "time_limit_ms": problem.time_limit_ms,
        "memory_limit_kb": problem.memory_limit_kb,
        "pids_limit": problem.pids_limit,
        "output_limit_in_bytes": problem.output_limit_in_bytes or "",
        "image_caption": problem.problem_image_caption or "",
    }


async def build_editor_context(
    request: Request,
    ctx: ContestAdminContext,
    problem: Problem,
    *,
    active_tab: str | None,
    errors: list[str] | None = None,
    field_errors: dict[str, str] | None = None,
    form_data: dict[str, Any] | None = None,
    md_content: str | None = None,
    editorial_content: str | None = None,
    has_pdf: bool | None = None,
    has_md: bool | None = None,
    category_names_csv: str | None = None,
    reselect_uploads: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build the editor template context for one rendering.

    Args:
        request: The active request.
        ctx: The contest-admin context.
        problem: The problem being edited.
        active_tab: The pane to open.
        errors: Validation messages to show, if any.
        field_errors: Validation messages keyed by form field name.
        form_data: Scalar values to render; the stored ones when omitted.
        md_content: Markdown to render in the editor; read from disk when omitted.
        editorial_content: Editorial Markdown to render; stored content when omitted.
        has_pdf: Override for whether a PDF statement is shown.
        has_md: Override for whether a Markdown statement is shown.
        category_names_csv: Categories to render; the stored ones when omitted.
        reselect_uploads: Archives the author must choose again.

    Returns:
        dict[str, Any]: The template context.
    """
    statement_dir = settings.PROBLEM_STATEMENT_DIR
    disk_has_pdf = await anyio.to_thread.run_sync(lambda: get_statement_path(problem.id, statement_dir).exists())
    disk_has_md = await anyio.to_thread.run_sync(lambda: get_md_statement_path(problem.id, statement_dir).exists())
    if md_content is None:
        md_content = ""
        if disk_has_md:
            md_content = await anyio.to_thread.run_sync(
                lambda: get_md_statement_path(problem.id, statement_dir).read_text(encoding="utf-8")
            )

    values = form_data if form_data is not None else stored_form_data(problem)
    if editorial_content is None:
        editorial_content = problem.editorial or ""
    resolved_field_errors = field_errors or {}
    profiling_limits_context = await _build_profiling_limits_context(request, ctx, problem, values)

    return {
        "current_user": ctx.actor,
        "problem": problem,
        "has_pdf": disk_has_pdf if has_pdf is None else has_pdf,
        "has_md": disk_has_md if has_md is None else has_md,
        "md_content": md_content,
        "editorial_content": editorial_content,
        "is_remove_allowed": _is_remove_allowed(ctx.contest),
        "remove_blocked_reason": _remove_blocked_reason(ctx.contest),
        "category_names_csv": (
            category_names_csv
            if category_names_csv is not None
            else ",".join(category.name for category in problem.categories)
        ),
        "errors": errors or [],
        "field_errors": resolved_field_errors,
        "first_error_field": next(iter(resolved_field_errors), ""),
        "success": False,
        "view": build_problem_form_view(
            request,
            slug=ctx.contest.login_slug,
            problem_id=problem.id,
            validator_type=problem.validator_type,
            active_tab=active_tab,
            reselect_uploads=reselect_uploads,
            problem_label=f"{_label(problem.ordinal)}. {problem.title}",
            save_disabled=not _is_edit_allowed(ctx.contest) and not _is_limits_edit_allowed(ctx.contest),
            solution_test_url=_solution_test_url(request, ctx, problem),
            notices=editor_notices(ctx.contest, has_problem=True),
        ),
        "balloon_colors": BALLOON_COLORS,
        "is_interactive": problem.validator_type is ProblemValidatorType.INTERACTIVE,
        **profiling_limits_context,
    }
