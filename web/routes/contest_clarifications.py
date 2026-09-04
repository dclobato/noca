#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select

from shared.enumerations import RoleEnum
from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.models.problem import Problem
from web.models.users import User
from web.routes.contest_clarifications_helpers import (
    _ALLOWED,
    _build_problem_map,
    _build_user_map,
    _html,
    _needs_user_map,
    _problem_map_from_list,
)
from web.services.clarification_service import (
    list_clarifications,
    mark_clarification_answers_read,
    normalize_clarification_sort,
)
from web.services.user_read_rate_limit import web_user_read_rate_limit

router = APIRouter(
    prefix="/c/{slug}/clarifications", tags=["contest_clarifications"], dependencies=[Depends(web_user_read_rate_limit)]
)


@router.post("/answers/read", status_code=204, name="contest_clarification_answers_read")
async def mark_answers_read(
    ctx: ContestContext = Depends(get_contest_context),
    clarification_ids: list[str] = Form(default_factory=list),
) -> Response:
    """Acknowledge what was rendered to the viewing team.

    Covers both kinds of notification the Clarifications list carries: answers to the
    team's own questions, and contest announcements. Announcement acknowledgement is
    idempotent, so a full page load racing the 60 s HTMX refresh is harmless.
    """
    ensure_allowed_role(ctx.actor, (RoleEnum.TEAM,))
    assert isinstance(ctx.actor, User)
    await mark_clarification_answers_read(
        ctx.session,
        ctx.contest,
        ctx.actor,
        clarification_ids,
    )
    await ctx.session.commit()
    return Response(status_code=204)


@router.get("/", response_class=HTMLResponse, name="contest_clarifications")
async def view(
    request: Request,
    ctx: ContestContext = Depends(get_contest_context),
    sort_by: str = Query("time_desc"),
) -> HTMLResponse:
    templates = request.app.state.templates
    ensure_allowed_role(ctx.actor, _ALLOWED)

    normalized_sort = normalize_clarification_sort(sort_by)

    clarifications, lock_service_available = await list_clarifications(
        ctx.session,
        ctx.contest,
        ctx.actor,
        request.app.state.valkey_runtime,
        normalized_sort,
    )

    result = await ctx.session.execute(
        select(Problem).where(Problem.contest_id == ctx.contest.id).order_by(Problem.ordinal)
    )
    problems = list(result.scalars().all())
    problem_map = _problem_map_from_list(problems)

    user_map = await _build_user_map(ctx.session, ctx.contest) if _needs_user_map(ctx.actor) else {}

    return _html(
        templates.TemplateResponse(
            request,
            "contest/clarifications.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "clarifications": clarifications,
                "lock_service_available": lock_service_available,
                "problems": problems,
                "problem_map": problem_map,
                "user_map": user_map,
                "sort_by": normalized_sort,
            },
        )
    )


@router.get("/list", response_class=HTMLResponse, name="contest_clarifications_list")
async def list_partial(
    request: Request,
    ctx: ContestContext = Depends(get_contest_context),
    sort_by: str = Query("time_desc"),
) -> HTMLResponse:
    templates = request.app.state.templates
    ensure_allowed_role(ctx.actor, _ALLOWED)
    normalized_sort = normalize_clarification_sort(sort_by)

    clarifications, lock_service_available = await list_clarifications(
        ctx.session,
        ctx.contest,
        ctx.actor,
        request.app.state.valkey_runtime,
        normalized_sort,
    )
    problem_map = await _build_problem_map(ctx.session, ctx.contest)

    return _html(
        templates.TemplateResponse(
            request,
            "contest/clarifications_list.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "clarifications": clarifications,
                "lock_service_available": lock_service_available,
                "problem_map": problem_map,
                "user_map": await _build_user_map(ctx.session, ctx.contest) if _needs_user_map(ctx.actor) else {},
                "sort_by": normalized_sort,
            },
        )
    )
