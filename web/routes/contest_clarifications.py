#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.models.problem import Problem
from web.routes.contest_clarifications_helpers import (
    _ALLOWED,
    _build_problem_map,
    _build_user_map,
    _html,
    _needs_user_map,
    _problem_map_from_list,
    _team_access_blocked,
)
from web.services.clarification_service import list_clarifications

router = APIRouter(prefix="/c/{slug}/clarifications", tags=["contest_clarifications"])


@router.get("/", response_class=HTMLResponse, name="contest_clarifications")
async def view(
    request: Request,
    ctx: ContestContext = Depends(get_contest_context),
) -> HTMLResponse:
    templates = request.app.state.templates
    ensure_allowed_role(ctx.actor, _ALLOWED)

    access_blocked = _team_access_blocked(ctx.actor, ctx.contest)

    if access_blocked:
        return _html(
            templates.TemplateResponse(
                request,
                "contest/clarifications.html",
                {
                    "current_user": ctx.actor,
                    "contest": ctx.contest,
                    "access_blocked": True,
                    "clarifications": [],
                    "lock_service_available": request.app.state.valkey_runtime.is_available,
                    "problems": [],
                    "problem_map": {},
                    "user_map": {},
                },
            )
        )

    clarifications, lock_service_available = await list_clarifications(
        ctx.session,
        ctx.contest,
        ctx.actor,
        request.app.state.valkey_runtime,
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
                "access_blocked": False,
                "clarifications": clarifications,
                "lock_service_available": lock_service_available,
                "problems": problems,
                "problem_map": problem_map,
                "user_map": user_map,
            },
        )
    )


@router.get("/list", response_class=HTMLResponse, name="contest_clarifications_list")
async def list_partial(request: Request, ctx: ContestContext = Depends(get_contest_context)) -> HTMLResponse:
    templates = request.app.state.templates
    ensure_allowed_role(ctx.actor, _ALLOWED)

    if _team_access_blocked(ctx.actor, ctx.contest):
        return _html(
            templates.TemplateResponse(
                request,
                "contest/clarifications_list.html",
                {
                    "current_user": ctx.actor,
                    "contest": ctx.contest,
                    "access_blocked": True,
                    "clarifications": [],
                    "lock_service_available": request.app.state.valkey_runtime.is_available,
                    "problem_map": {},
                    "user_map": {},
                },
            )
        )

    clarifications, lock_service_available = await list_clarifications(
        ctx.session,
        ctx.contest,
        ctx.actor,
        request.app.state.valkey_runtime,
    )
    problem_map = await _build_problem_map(ctx.session, ctx.contest)

    return _html(
        templates.TemplateResponse(
            request,
            "contest/clarifications_list.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "access_blocked": False,
                "clarifications": clarifications,
                "lock_service_available": lock_service_available,
                "problem_map": problem_map,
                "user_map": await _build_user_map(ctx.session, ctx.contest) if _needs_user_map(ctx.actor) else {},
            },
        )
    )
