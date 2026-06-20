#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import asyncio
import json as _json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy import and_, func, or_, select

from shared.enumerations import JudgmentStatus, RoleEnum
from web.dependencies import ContestContext, get_contest_context
from web.models.clarification import Clarification
from web.models.contest import Task
from web.models.problem import Problem
from web.models.submission import Submission, SubmissionJudgment
from web.models.users import User
from web.services.contest_service import build_contest_clock_payload

router = APIRouter(prefix="/c/{slug}", tags=["contest_dashboard"])


def _html(response: object) -> HTMLResponse:
    return cast(HTMLResponse, response)


@dataclass(frozen=True)
class DashboardCounters:
    clarifications_pending: int | None
    runs_without_verdict: int | None
    tasks_pending: int | None


async def _build_clarifications_pending_count(ctx: ContestContext, team_id: str | None = None) -> int:
    query = (
        select(func.count(Clarification.id))
        .join(Problem, Clarification.problem_id == Problem.id)
        .where(
            and_(
                Problem.contest_id == ctx.contest.id,
                Clarification.answered_at.is_(None),
                Clarification.hidden.is_(False),
            )
        )
    )
    if team_id is not None:
        query = query.where(Clarification.team_id == team_id)
    result = await ctx.session.execute(query)
    count = result.scalar() or 0
    return int(count)


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
        team_id = actor.id if role == RoleEnum.TEAM else None

        clarifications_pending: int | None = None
        runs_without_verdict: int | None = None
        tasks_pending: int | None = None

        if role in (RoleEnum.ADMIN, RoleEnum.UBERADMIN, RoleEnum.JUDGE):
            clarifications_pending = await _build_clarifications_pending_count(ctx)
            runs_without_verdict = await _build_runs_without_verdict_count(ctx)
            tasks_pending = await _build_tasks_pending_count(ctx)
        elif role == RoleEnum.STAFF:
            tasks_pending = await _build_tasks_pending_count(ctx)
        elif role == RoleEnum.TEAM:
            clarifications_pending = await _build_clarifications_pending_count(ctx, team_id)
            runs_without_verdict = await _build_runs_without_verdict_count(ctx, team_id)
            tasks_pending = await _build_tasks_pending_count(ctx, team_id)

        counters = DashboardCounters(
            clarifications_pending=clarifications_pending,
            runs_without_verdict=runs_without_verdict,
            tasks_pending=tasks_pending,
        )

    return _html(
        templates.TemplateResponse(
            request, "contest/dashboard.html", {"current_user": actor, "contest": ctx.contest, "counters": counters}
        )
    )


@router.get("/clock")
async def contest_clock(request: Request, ctx: ContestContext = Depends(get_contest_context)) -> Response:
    if "text/event-stream" in request.headers.get("accept", ""):
        contest = ctx.contest  # already loaded into memory; session not needed during streaming
        await ctx.session.close()  # release DB connection before entering the long-lived stream

        async def _stream() -> AsyncIterator[str]:
            while not await request.is_disconnected():
                yield f"data: {_json.dumps(build_contest_clock_payload(contest))}\n\n"
                await asyncio.sleep(30)

        return StreamingResponse(_stream(), media_type="text/event-stream")

    return Response(content=_json.dumps(build_contest_clock_payload(ctx.contest)), media_type="application/json")
