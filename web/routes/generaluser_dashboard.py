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
from shared.services.scoreboard_projection import ordinal_to_label
from web.dependencies import ContestContext, get_contest_context
from web.models.contest import Task
from web.models.problem import Problem
from web.models.submission import Submission, SubmissionJudgment
from web.models.users import User
from web.services.clarification_service import (
    count_pending_clarifications,
    count_unread_team_clarifications,
)
from web.services.contest_service import build_contest_clock_payload, build_contest_rules_summary
from web.services.first_solve_service import accepted_verdicts, first_accepted_submission_ids_by_problem

router = APIRouter(prefix="/c/{slug}", tags=["contest_dashboard"])


def _html(response: object) -> HTMLResponse:
    return cast(HTMLResponse, response)


@dataclass(frozen=True)
class DashboardCounters:
    clarifications_attention: int | None
    runs_without_verdict: int | None
    tasks_pending: int | None


@dataclass(frozen=True, slots=True)
class DashboardSolvedProblem:
    """One solved problem displayed in the team dashboard identity panel.

    Attributes:
        problem_id: Stable problem identifier.
        label: Contest-order problem label.
        title: Problem title used by the accessible link label.
        color: Balloon color without the leading hash, ready for the asset URL.
        is_first_solve: Whether this team's solve was the contest's first for
            the problem.
    """

    problem_id: str
    label: str
    title: str
    color: str
    is_first_solve: bool


async def _build_team_solved_problems(ctx: ContestContext, team_id: str) -> list[DashboardSolvedProblem]:
    """Return the team's solved problems in contest order.

    The query uses authoritative active judgments rather than the public
    scoreboard projection, so the team's own achievements remain accurate
    during a scoreboard freeze.

    Args:
        ctx: Active contest request context.
        team_id: Team whose accepted problems should be returned.

    Returns:
        One row per solved problem, ordered by problem ordinal.
    """
    result = await ctx.session.execute(
        select(Problem, Submission.id)
        .join(Submission, Submission.problem_id == Problem.id)
        .join(SubmissionJudgment, SubmissionJudgment.submission_id == Submission.id)
        .where(
            Problem.contest_id == ctx.contest.id,
            Submission.team_id == team_id,
            SubmissionJudgment.status != JudgmentStatus.SUPERSEDED,
            SubmissionJudgment.final_verdict.in_(accepted_verdicts(accept_pe=ctx.contest.accept_pe)),
        )
        .order_by(
            Problem.ordinal,
            Submission.timestamp_seconds,
            Submission.created_at,
            Submission.id,
        )
    )
    first_solve_ids = await first_accepted_submission_ids_by_problem(ctx.session, ctx.contest)
    solved_problems: list[DashboardSolvedProblem] = []
    seen_problem_ids: set[str] = set()

    for problem, submission_id in result.all():
        problem_id = str(problem.id)
        if problem_id in seen_problem_ids:
            continue
        seen_problem_ids.add(problem_id)
        solved_problems.append(
            DashboardSolvedProblem(
                problem_id=problem_id,
                label=ordinal_to_label(problem.ordinal),
                title=problem.title,
                color=problem.color.lstrip("#") if problem.color else "888888",
                is_first_solve=first_solve_ids.get(problem_id) == str(submission_id),
            )
        )

    return solved_problems


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
    solved_problems: list[DashboardSolvedProblem] = []

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
            clarifications_attention = await count_unread_team_clarifications(
                ctx.session,
                ctx.contest,
                actor.id,
            )
            runs_without_verdict = await _build_runs_without_verdict_count(ctx, actor.id)
            tasks_pending = await _build_tasks_pending_count(ctx, actor.id)
            solved_problems = await _build_team_solved_problems(ctx, actor.id)

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
                "solved_problems": solved_problems,
                "rules": build_contest_rules_summary(ctx.contest),
            },
        )
    )


@router.get("/clock")
async def contest_clock(ctx: ContestContext = Depends(get_contest_context)) -> JSONResponse:
    """Return the current contest clock snapshot for periodic browser polling."""
    return JSONResponse(build_contest_clock_payload(ctx.contest))
