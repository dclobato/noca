#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from dataclasses import dataclass
from typing import cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import and_, func, or_, select

from shared.enumerations import JudgmentStatus, RoleEnum
from web.dependencies import ContestContext, get_contest_context
from web.models.contest import Task
from web.models.problem import Problem
from web.models.submission import Submission, SubmissionJudgment
from web.models.users import User
from web.services.clarification_service import (
    count_pending_clarifications,
    count_unread_clarification_answers,
)
from web.services.contest_service import build_contest_clock_payload, build_contest_rules_summary

router = APIRouter(prefix="/c/{slug}", tags=["contest_dashboard"])


def _html(response: object) -> HTMLResponse:
    return cast(HTMLResponse, response)


@dataclass(frozen=True)
class DashboardCounters:
    clarifications_attention: int | None
    runs_without_verdict: int | None
    tasks_pending: int | None


async def _build_runs_without_verdict_count(ctx: ContestContext, team_id: str | None = None) -> int:
    query = (
        select(func.count(SubmissionJudgment.id))
        .join(Submission, SubmissionJudgment.submission_id == Submission.id)
        .join(Problem, Submission.problem_id == Problem.id)
        .where(
            and_(
                Problem.contest_id == ctx.contest.id,
                SubmissionJudgment.status == JudgmentStatus.DONE,
                SubmissionJudgment.final_verdict.is_(None),
            )
        )
    )
    if team_id is not None:
        query = query.where(Submission.team_id == team_id)
    result = await ctx.session.execute(query)
    count = result.scalar() or 0
    return int(count)


async def _build_tasks_pending_count(ctx: ContestContext, team_id: str | None = None) -> int:
    query = (
        select(func.count(Task.id))
        .outerjoin(Problem, Task.problem_id == Problem.id)
        .outerjoin(User, Task.team_id == User.id)
        .where(
            and_(
                Task.finished_at.is_(None),
                or_(
                    Problem.contest_id == ctx.contest.id,
                    and_(Task.problem_id.is_(None), User.contest_id == ctx.contest.id),
                ),
            )
        )
    )
    if team_id is not None:
        query = query.where(Task.team_id == team_id)
    result = await ctx.session.execute(query)
    count = result.scalar() or 0
    return int(count)


@router.get("/", response_class=HTMLResponse, name="contest_dashboard")
async def dashboard(request: Request, ctx: ContestContext = Depends(get_contest_context)) -> HTMLResponse:
    templates = request.app.state.templates

    actor = ctx.actor
    counters: DashboardCounters | None = None

    if isinstance(actor, User):
        role = actor.role

        clarifications_attention: int | None = None
        runs_without_verdict: int | None = None
        tasks_pending: int | None = None

        if role in (RoleEnum.ADMIN, RoleEnum.UBERADMIN, RoleEnum.JUDGE):
            clarifications_attention = await count_pending_clarifications(ctx.session, ctx.contest)
            runs_without_verdict = await _build_runs_without_verdict_count(ctx)
            tasks_pending = await _build_tasks_pending_count(ctx)
        elif role == RoleEnum.STAFF:
            tasks_pending = await _build_tasks_pending_count(ctx)
        elif role == RoleEnum.TEAM:
            clarifications_attention = await count_unread_clarification_answers(
                ctx.session,
                ctx.contest,
                actor.id,
            )
            runs_without_verdict = await _build_runs_without_verdict_count(ctx, actor.id)
            tasks_pending = await _build_tasks_pending_count(ctx, actor.id)

        counters = DashboardCounters(
            clarifications_attention=clarifications_attention,
            runs_without_verdict=runs_without_verdict,
            tasks_pending=tasks_pending,
        )

    return _html(
        templates.TemplateResponse(
            request,
            "contest/dashboard.html",
            {
                "current_user": actor,
                "contest": ctx.contest,
                "counters": counters,
                "rules": build_contest_rules_summary(ctx.contest),
            },
        )
    )


@router.get("/clock")
async def contest_clock(ctx: ContestContext = Depends(get_contest_context)) -> JSONResponse:
    """Return the current contest clock snapshot for periodic browser polling."""
    return JSONResponse(build_contest_clock_payload(ctx.contest))
