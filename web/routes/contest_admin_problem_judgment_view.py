#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""URLs and template context for the Contest judgment-data pages.

The shared partials resolve no Contest route names, so every URL a judgment page
needs is built here and handed over in a view model -- the arrangement
``shared.services.testcase_view.TestCaseRowView`` already uses for the row table.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

import anyio
from fastapi import Request
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import ProblemValidatorType
from shared.services.custom_validator import ValidatorStatusView, status_view
from shared.services.judgment_page_view import (
    InteractionsPageView,
    JudgmentShellView,
    TestCasesPageView,
    ValidatorPageView,
    build_judgment_nav,
    build_judgment_readiness,
)
from shared.services.problem_definition_view import label_for_strategy
from shared.services.problem_editor_header import EditorLink, ProblemEditorHeaderView
from shared.services.problem_judgeability import ProblemJudgeabilityFacts
from shared.services.sample_interactions import MAX_SAMPLE_INTERACTIONS
from web.config import settings
from web.dependencies import ContestAdminContext
from web.models.problem import Problem
from web.models.submission import Submission
from web.routes.contest_admin_problem_helpers import (
    _is_edit_allowed,
    _label,
    _read_testcase_preview_for,
    build_testcase_row_views,
)
from web.routes.contest_admin_problem_interactions import build_interaction_row_views
from web.routes.contest_admin_problem_judgment_urls import PAGE_ENDPOINTS, judgment_page_url
from web.services.problem_service import (
    get_active_languages,
    get_problem_in_contest,
    load_contest_problem_judgeability_facts,
    load_sample_interactions,
)


async def build_judgment_context(
    request: Request,
    ctx: ContestAdminContext,
    problem_id: str,
    *,
    active_page: str,
) -> dict[str, Any]:
    """Build the template context for one judgment page.

    Args:
        request: The active request.
        ctx: The contest-admin context.
        problem_id: The problem being edited.
        active_page: Which page is being rendered.

    Returns:
        dict[str, Any]: Context for the page template.

    Raises:
        Exception: If the problem does not belong to this contest.
    """
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        raise Exception("Problem not found")

    slug = ctx.contest.login_slug
    interactions = await load_sample_interactions(ctx.session, problem.id)
    validator_status = status_view(problem.custom_validator)
    has_submissions = await _has_submissions(ctx.session, problem.id)
    judgeability_facts = await load_contest_problem_judgeability_facts(ctx.session, problem.id)
    shell = _build_shell(
        request,
        ctx,
        problem,
        active_page=active_page,
        interaction_count=len(interactions),
        validator_status=validator_status,
        judgeability_facts=judgeability_facts,
        has_submissions=has_submissions,
    )

    context: dict[str, Any] = {
        "current_user": ctx.actor,
        "contest": ctx.contest,
        "problem": problem,
        "shell": shell,
        "is_edit_allowed": shell.is_edit_allowed,
    }
    if active_page == "test-cases":
        context |= await _test_cases_context(request, ctx, problem)
    elif active_page == "validator":
        context |= _validator_context(
            request,
            slug,
            problem,
            validator_status=validator_status,
            interaction_count=len(interactions),
        )
        languages = await get_active_languages(ctx.session)
        context["validator_languages"] = languages
        context["validator_language_extensions"] = {
            language.id: PurePosixPath(language.source_filename).suffix for language in languages
        }
        context["interaction_rows"] = build_interaction_row_views(request, ctx.contest, interactions)
    else:
        context |= _interactions_context(request, slug, problem, interaction_count=len(interactions))
        context["interaction_rows"] = build_interaction_row_views(request, ctx.contest, interactions)
    return context


def _build_shell(
    request: Request,
    ctx: ContestAdminContext,
    problem: Problem,
    *,
    active_page: str,
    interaction_count: int,
    validator_status: ValidatorStatusView,
    judgeability_facts: ProblemJudgeabilityFacts,
    has_submissions: bool,
) -> JudgmentShellView:
    """Build the chrome: header, strategy badge, page navigation."""
    slug = ctx.contest.login_slug
    urls = {key: judgment_page_url(request, slug, problem.id, key) for key in PAGE_ENDPOINTS}
    badges: dict[str, str] = {}
    if not problem.test_cases:
        badges["test-cases"] = "none yet"
    if problem.validator_type is ProblemValidatorType.INTERACTIVE:
        if problem.custom_validator is None:
            badges["validator"] = "none yet"
        if interaction_count == 0:
            badges["interactions"] = "none yet"
    problem_label = f"{_label(problem.ordinal)}. {problem.title}"
    definition_url = str(request.url_for("edit_problem_form", slug=slug, problem_id=problem.id))
    return JudgmentShellView(
        header=ProblemEditorHeaderView(
            title="Judgment data",
            # Contest has no publication state to set from here, so the bar holds
            # the same Back link and trailing group and simply no submitters.
            form_id="",
            subtitle=problem_label,
            icon="rule",
            back_url=str(request.url_for("manage_problems", slug=slug)),
            links=(EditorLink(label="Problem definition", url=definition_url, icon="edit"),),
            strategy_label=label_for_strategy(problem.validator_type),
        ),
        problem_label=problem_label,
        validator_type=problem.validator_type,
        is_interactive=problem.validator_type is ProblemValidatorType.INTERACTIVE,
        is_edit_allowed=_is_edit_allowed(ctx.contest),
        read_only_reason=(
            ""
            if _is_edit_allowed(ctx.contest)
            else "This contest has started, so its judgment data can be viewed but not changed."
        ),
        active_page=active_page,
        pages=build_judgment_nav(validator_type=problem.validator_type, urls=urls, badges=badges),
        readiness=build_judgment_readiness(
            facts=judgeability_facts,
            validator_status=validator_status,
            has_submissions=has_submissions,
        ),
        definition_url=definition_url,
        problem_list_url=str(request.url_for("manage_problems", slug=slug)),
    )


async def _test_cases_context(
    request: Request,
    ctx: ContestAdminContext,
    problem: Problem,
) -> dict[str, Any]:
    """Rows, previews and the page's own action URLs."""
    slug = ctx.contest.login_slug
    testcase_dir = settings.PROBLEM_TESTCASE_DIR
    previews: dict[str, tuple[str, str]] = {}
    for test_case in problem.test_cases:
        previews[test_case.id] = await anyio.to_thread.run_sync(
            _read_testcase_preview_for(problem.id, test_case.ordinal, testcase_dir)
        )
    return {
        "rows": build_testcase_row_views(request, ctx.contest, list(problem.test_cases), previews),
        "interactive": problem.validator_type is ProblemValidatorType.INTERACTIVE,
        "page": TestCasesPageView(
            bulk_url=str(request.url_for("problem_judgment_cases_replace_all", slug=slug, problem_id=problem.id)),
            upload_url=str(request.url_for("problem_judgment_case_upload", slug=slug, problem_id=problem.id)),
            add_url=str(request.url_for("problem_judgment_cases_save", slug=slug, problem_id=problem.id)),
            case_count=len(problem.test_cases),
        ),
    }


async def _has_submissions(session: AsyncSession, problem_id: str) -> bool:
    """Whether anything has been judged against this problem's current test data.

    An existence probe rather than a load: the page needs one boolean, and a
    problem in a live contest may have many thousands of submissions.
    """
    return bool(await session.scalar(select(exists().where(Submission.problem_id == problem_id))))


def _validator_context(
    request: Request,
    slug: str,
    problem: Problem,
    *,
    validator_status: ValidatorStatusView,
    interaction_count: int,
) -> dict[str, Any]:
    """Validator status, source links and action URLs."""
    return {
        "validator_status": validator_status,
        "page": ValidatorPageView(
            upload_url=str(request.url_for("upload_problem_custom_validator", slug=slug, problem_id=problem.id)),
            remove_url=str(request.url_for("remove_problem_custom_validator", slug=slug, problem_id=problem.id)),
            download_url=str(request.url_for("download_problem_custom_validator", slug=slug, problem_id=problem.id)),
            source_url=str(request.url_for("view_problem_custom_validator_source", slug=slug, problem_id=problem.id)),
            status_template="admin/problems/_validator_status.html",
            interaction_count=interaction_count,
        ),
    }


def _interactions_context(request: Request, slug: str, problem: Problem, *, interaction_count: int) -> dict[str, Any]:
    """Sample-interaction action URLs and the cap."""
    return {
        "page": InteractionsPageView(
            add_url=str(request.url_for("problem_judgment_interactions_save", slug=slug, problem_id=problem.id)),
            max_interactions=MAX_SAMPLE_INTERACTIONS,
            interaction_count=interaction_count,
        ),
        "max_interactions": MAX_SAMPLE_INTERACTIONS,
    }
