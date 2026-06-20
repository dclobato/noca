#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import contextlib
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import and_, func, or_, select
from werkzeug.security import check_password_hash

from shared.enumerations import JudgmentStatus
from shared.services.lock_service import get_locks
from web.dependencies import ContestAdminContext
from web.models.clarification import Clarification
from web.models.contest import Contest, Task
from web.models.problem import Problem
from web.models.submission import Submission, SubmissionJudgment
from web.models.users import UberAdmin, User
from web.services.valkey_service import get_contest_queue_metrics


def _html(response: object) -> HTMLResponse:
    return cast(HTMLResponse, response)


def _compute_end_now_duration_minutes(contest_start_time: datetime, now: datetime) -> int:
    """Compute the shortest integer-minute duration that ends at or after now.

    Args:
        contest_start_time: Contest start timestamp.
        now: Current timestamp used as the forced end point.

    Returns:
        The smallest duration in whole minutes that does not end before `now`.
    """
    elapsed_seconds = max(0.0, (now - contest_start_time).total_seconds())
    return max(1, int((elapsed_seconds + 59) // 60))


def _end_contest_now(contest: Contest, *, now: datetime) -> None:
    """Adjust contest timing fields so the contest ends immediately.

    Args:
        contest: Contest instance to update in memory.
        now: Current timestamp used to compute the new duration.

    Returns:
        None.
    """
    new_duration = _compute_end_now_duration_minutes(contest.start_time, now)
    contest.duration_minutes = new_duration
    contest.stop_updating_scoreboard = min(contest.stop_updating_scoreboard, new_duration)
    contest.stop_answers_after = min(contest.stop_answers_after, new_duration)
    timeout_limit = max(0, new_duration - 1)
    contest.clarifications_timeout_minutes = min(contest.clarifications_timeout_minutes, timeout_limit)
    contest.tasks_timeout_minutes = min(contest.tasks_timeout_minutes, timeout_limit)
    contest.review_timeout_minutes = min(contest.review_timeout_minutes, timeout_limit)


def _is_actor_password_valid(actor: User | UberAdmin, password: str) -> bool:
    """Validate the authenticated actor password.

    Args:
        actor: Authenticated admin-capable actor.
        password: Raw password submitted in the confirmation form.

    Returns:
        `True` when the password matches the actor credentials.
    """
    return bool(password) and check_password_hash(actor.password_hash, password)


@dataclass(frozen=True)
class RunCounters:
    """Aggregated run counters shown in contest admin metrics page."""

    total: int
    on_queue_autojudge: int
    autojudging: int
    autojudge_finished: int
    on_human_review_queue: int
    done: int


@dataclass(frozen=True)
class TaskCounters:
    """Aggregated task counters shown in contest admin metrics page."""

    total: int
    done: int
    on_service: int | None
    on_queue: int | None
    lock_service_available: bool


@dataclass(frozen=True)
class ClarificationCounters:
    """Clarification counters excluding announcements."""

    total: int
    waiting_answer: int
    answered: int


@dataclass(frozen=True)
class QueueCounters:
    """Contest-scoped queue counters read from Valkey."""

    profiling: int | None
    priority: int | None
    pending: int | None
    inflight: int | None
    total: int | None
    service_available: bool


@dataclass(frozen=True)
class ContestAdminCounters:
    """All counter groups rendered on contest admin counters page."""

    runs: RunCounters
    tasks: TaskCounters
    clarifications: ClarificationCounters
    announcements_total: int
    queue_sizes: QueueCounters


async def _build_run_counters(ctx: ContestAdminContext) -> RunCounters:
    """Build run counters scoped to one contest."""
    result = await ctx.session.execute(
        select(
            SubmissionJudgment.status,
            SubmissionJudgment.final_verdict,
            func.count(),
        )
        .join(Submission, SubmissionJudgment.submission_id == Submission.id)
        .join(Problem, Submission.problem_id == Problem.id)
        .where(Problem.contest_id == ctx.contest.id)
        .group_by(SubmissionJudgment.status, SubmissionJudgment.final_verdict)
    )

    total = 0
    on_queue_autojudge = 0
    autojudging = 0
    autojudge_finished = 0
    on_human_review_queue = 0
    done = 0

    for status, final_verdict, count in result.all():
        current = int(count)
        total += current

        if status == JudgmentStatus.QUEUED:
            on_queue_autojudge += current
        elif status in (JudgmentStatus.DISPATCHED, JudgmentStatus.JUDGING):
            autojudging += current

        if status == JudgmentStatus.DONE:
            autojudge_finished += current
            if final_verdict is None:
                on_human_review_queue += current
            else:
                done += current

    return RunCounters(
        total=total,
        on_queue_autojudge=on_queue_autojudge,
        autojudging=autojudging,
        autojudge_finished=autojudge_finished,
        on_human_review_queue=on_human_review_queue,
        done=done,
    )


async def _build_task_counters(request: Request, ctx: ContestAdminContext) -> TaskCounters:
    """Build task counters scoped to one contest."""
    result = await ctx.session.execute(
        select(Task.id, Task.finished_at)
        .outerjoin(Problem, Task.problem_id == Problem.id)
        .outerjoin(User, Task.team_id == User.id)
        .where(
            or_(
                Problem.contest_id == ctx.contest.id,
                and_(Task.problem_id.is_(None), User.contest_id == ctx.contest.id),
            )
        )
    )

    total = 0
    done = 0
    unfinished_ids: list[str] = []
    for task_id, finished_at in result.all():
        total += 1
        if finished_at is None:
            unfinished_ids.append(task_id)
        else:
            done += 1

    if not unfinished_ids:
        return TaskCounters(total=total, done=done, on_service=0, on_queue=0, lock_service_available=True)

    lock_batch = await get_locks(
        request.app.state.valkey_runtime,
        kind="task",
        contest_id=ctx.contest.id,
        resource_ids=unfinished_ids,
    )
    if not lock_batch.service_available:
        return TaskCounters(
            total=total,
            done=done,
            on_service=None,
            on_queue=None,
            lock_service_available=False,
        )

    on_service = len(lock_batch.locks_by_resource_id)
    on_queue = max(0, len(unfinished_ids) - on_service)
    return TaskCounters(
        total=total,
        done=done,
        on_service=on_service,
        on_queue=on_queue,
        lock_service_available=True,
    )


async def _build_clarification_counters(ctx: ContestAdminContext) -> tuple[ClarificationCounters, int]:
    """Build clarification and announcement counters for one contest."""
    result = await ctx.session.execute(
        select(Clarification.question, Clarification.answered_at)
        .join(Problem, Clarification.problem_id == Problem.id)
        .where(Problem.contest_id == ctx.contest.id)
    )

    total = 0
    waiting_answer = 0
    answered = 0
    announcements_total = 0
    for question, answered_at in result.all():
        if question == "Announcement":
            announcements_total += 1
            continue
        total += 1
        if answered_at is None:
            waiting_answer += 1
        else:
            answered += 1

    return ClarificationCounters(total=total, waiting_answer=waiting_answer, answered=answered), announcements_total


async def _build_queue_counters(request: Request, ctx: ContestAdminContext) -> QueueCounters:
    """Build contest-scoped queue counters using Valkey service."""
    metrics = await get_contest_queue_metrics(request.app.state.valkey_runtime, str(ctx.contest.id))
    if metrics is None:
        return QueueCounters(
            profiling=None,
            priority=None,
            pending=None,
            inflight=None,
            total=None,
            service_available=False,
        )
    return QueueCounters(
        profiling=metrics.profiling_count,
        priority=metrics.priority_count,
        pending=metrics.pending_count,
        inflight=metrics.inflight_count,
        total=metrics.total_count,
        service_available=True,
    )


async def _build_contest_admin_counters(request: Request, ctx: ContestAdminContext) -> ContestAdminCounters:
    """Build all counter groups needed by the counters page."""
    runs = await _build_run_counters(ctx)
    tasks = await _build_task_counters(request, ctx)
    clarifications, announcements_total = await _build_clarification_counters(ctx)
    queue_sizes = await _build_queue_counters(request, ctx)
    return ContestAdminCounters(
        runs=runs,
        tasks=tasks,
        clarifications=clarifications,
        announcements_total=announcements_total,
        queue_sizes=queue_sizes,
    )


async def _clear_frozen_scoreboard_snapshot(request: Request, contest_id: str) -> None:
    """Best-effort cache invalidation after metadata changes."""
    from shared.services.scoreboard_cache import scoreboard_frozen_key

    with contextlib.suppress(Exception):
        await request.app.state.valkey_runtime.delete(scoreboard_frozen_key(contest_id))
