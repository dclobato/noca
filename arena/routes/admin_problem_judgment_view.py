#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""URLs and template context for the Arena judgment-data pages.

The Arena half of what ``web.routes.contest_admin_problem_judgment_view`` does for
Contest: the shared partials resolve no Arena route names, so every URL is built
here and handed over in a view model.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from fastapi import Request
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_submissions import ArenaSubmission
from arena.models.arena_users import ArenaUser
from arena.routes.admin_problem_common import validator_languages
from arena.routes.admin_problem_form_views import build_interaction_row_views, build_testcase_row_views
from arena.routes.admin_problem_judgment_urls import PAGE_ENDPOINTS, judgment_page_url, with_query
from arena.services import admin_problem_interaction_service, admin_problem_tc_service
from shared.enumerations import ArenaRole, ProblemValidatorType
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
from shared.services.problem_judgeability import ProblemJudgeabilityFacts
from shared.services.sample_interactions import MAX_SAMPLE_INTERACTIONS


async def build_judgment_context(
    request: Request,
    session: AsyncSession,
    problem: ArenaProblem,
    *,
    active_page: str,
    current_user: ArenaUser,
) -> dict[str, Any]:
    """Build the template context for one Arena judgment page.

    Args:
        request: The active request.
        session: Open Arena session.
        problem: The problem being edited.
        active_page: Which page is being rendered.
        current_user: The signed-in editor. Required rather than optional: the
            Arena chrome renders its whole sidebar and the account menu from it,
            so a page that omits it loses most of its navigation.

    Returns:
        dict[str, Any]: Context for the page template.
    """
    interactions = await admin_problem_interaction_service.list_interactions(session, problem.id)
    test_cases = await admin_problem_tc_service.list_testcase_views(session, problem.id, settings.PROBLEM_TESTCASE_DIR)
    validator_status = status_view(problem.custom_validator)
    has_submissions = bool(await session.scalar(select(exists().where(ArenaSubmission.problem_id == problem.id))))
    judgeability_facts = await admin_problem_tc_service.judgeability_facts_for(session, problem)
    shell = _build_shell(
        request,
        problem,
        active_page=active_page,
        case_count=len(test_cases),
        interaction_count=len(interactions),
        validator_status=validator_status,
        judgeability_facts=judgeability_facts,
        has_submissions=has_submissions,
    )

    context: dict[str, Any] = {
        "problem": problem,
        "shell": shell,
        "current_user": current_user,
        "is_admin": current_user.role is ArenaRole.ARENA_ADMIN,
        # Arena has no running-contest lock: any problem editor may change the
        # judgment data of a problem they own.
        "is_edit_allowed": True,
    }
    # The problem list's page, filters, and sort ride along on every action URL
    # here, exactly as they do on the shell's tab and definition-editor links, so
    # a save/upload/delete redirect back to this page never drops them either.
    query = request.url.query
    if active_page == "test-cases":
        context |= {
            "rows": build_testcase_row_views(request, problem.id, test_cases),
            "interactive": problem.validator_type is ProblemValidatorType.INTERACTIVE,
            "page": TestCasesPageView(
                bulk_url=with_query(
                    str(request.url_for("arena_admin_problem_judgment_cases_replace_all", problem_id=problem.id)),
                    query,
                ),
                upload_url=with_query(
                    str(request.url_for("arena_admin_problem_judgment_case_upload", problem_id=problem.id)), query
                ),
                add_url=with_query(
                    str(request.url_for("arena_admin_problem_judgment_cases_save", problem_id=problem.id)), query
                ),
                case_count=len(test_cases),
            ),
        }
    elif active_page == "validator":
        languages = await validator_languages(session)
        context |= {
            "validator_status": validator_status,
            "validator_languages": languages,
            "validator_language_extensions": {
                language.id: PurePosixPath(language.source_filename).suffix for language in languages
            },
            "interaction_rows": build_interaction_row_views(request, problem.id, interactions),
            "page": ValidatorPageView(
                upload_url=with_query(
                    str(request.url_for("arena_admin_problem_validator_upload", problem_id=problem.id)), query
                ),
                remove_url=with_query(
                    str(request.url_for("arena_admin_problem_validator_remove", problem_id=problem.id)), query
                ),
                download_url=with_query(
                    str(request.url_for("arena_admin_problem_validator_download", problem_id=problem.id)), query
                ),
                source_url=with_query(
                    str(request.url_for("arena_admin_problem_validator_source_view", problem_id=problem.id)), query
                ),
                status_template="admin/_validator_status.html",
                interaction_count=len(interactions),
            ),
        }
    else:
        context |= {
            "interaction_rows": build_interaction_row_views(request, problem.id, interactions),
            "page": InteractionsPageView(
                add_url=with_query(
                    str(request.url_for("arena_admin_problem_judgment_interactions_save", problem_id=problem.id)),
                    query,
                ),
                max_interactions=MAX_SAMPLE_INTERACTIONS,
                interaction_count=len(interactions),
            ),
            "max_interactions": MAX_SAMPLE_INTERACTIONS,
        }
    return context


def _build_shell(
    request: Request,
    problem: ArenaProblem,
    *,
    active_page: str,
    case_count: int,
    interaction_count: int,
    validator_status: ValidatorStatusView,
    judgeability_facts: ProblemJudgeabilityFacts,
    has_submissions: bool,
) -> JudgmentShellView:
    """Build the chrome: header, strategy badge, page navigation."""
    urls = {key: judgment_page_url(request, problem.id, key) for key in PAGE_ENDPOINTS}
    badges: dict[str, str] = {}
    if case_count == 0:
        badges["test-cases"] = "none yet"
    if problem.validator_type is ProblemValidatorType.INTERACTIVE:
        if problem.custom_validator is None:
            badges["validator"] = "none yet"
        if interaction_count == 0:
            badges["interactions"] = "none yet"
    return JudgmentShellView(
        problem_label=f"#{problem.arena_number} {problem.title}",
        strategy_label=label_for_strategy(problem.validator_type),
        validator_type=problem.validator_type,
        is_interactive=problem.validator_type is ProblemValidatorType.INTERACTIVE,
        is_edit_allowed=True,
        active_page=active_page,
        pages=build_judgment_nav(validator_type=problem.validator_type, urls=urls, badges=badges),
        readiness=build_judgment_readiness(
            facts=judgeability_facts,
            validator_status=validator_status,
            has_submissions=has_submissions,
            rejudge_url=str(request.url_for("arena_admin_problem_rejudge_all", problem_id=problem.id)),
            rejudge_return_path=request.url_for(PAGE_ENDPOINTS[active_page], problem_id=problem.id).path,
        ),
        definition_url=with_query(
            str(request.url_for("arena_admin_problem_edit", problem_id=problem.id)), request.url.query
        ),
        problem_list_url=str(request.url_for("arena_admin_problem_list")),
    )
