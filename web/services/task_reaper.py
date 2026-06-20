#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import asyncio
import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from web.models._base import _utcnow
from web.models.contest import Contest, Task
from web.models.problem import Problem
from web.models.users import User
from web.services.reaper_runner import run_reaper_loop
from web.services.time_utils import normalize_now_for_reference

module_logger = logging.getLogger(__name__)


async def release_expired_tasks(
    session: AsyncSession,
) -> int:
    """Legacy no-op reaper kept for startup compatibility after Valkey lock migration."""
    del session
    return 0


async def conclude_finished_contest_tasks(
    session: AsyncSession,
    now: datetime.datetime | None = None,
) -> int:
    """Auto-finish unfinished tasks for contests that have already ended.

    The finishing actor is recorded as the contest owner admin. Contests without
    an owner are skipped.
    """
    current_time = now or _utcnow()

    result_with_problem = await session.execute(
        select(Task, Contest)
        .join(Problem, Task.problem_id == Problem.id)
        .join(Contest, Problem.contest_id == Contest.id)
        .where(
            Task.finished_at.is_(None),
        )
    )

    result_sos = await session.execute(
        select(Task, Contest)
        .join(User, Task.team_id == User.id)
        .join(Contest, User.contest_id == Contest.id)
        .where(
            Task.problem_id.is_(None),
            Task.finished_at.is_(None),
        )
    )

    concluded = 0
    seen_task_ids: set[str] = set()
    for task, contest in list(result_with_problem.all()) + list(result_sos.all()):
        if task.id in seen_task_ids:
            continue
        seen_task_ids.add(task.id)

        if not contest.is_past:
            continue
        if contest.owner_user_id is None:
            module_logger.warning(f"Skipping post-contest task conclusion because contest '{contest.id}' has no owner")
            continue

        task.staff_id = contest.owner_user_id
        task.finished_at = normalize_now_for_reference(current_time, task.created_at)
        task.finished_timestamp_seconds = max(0, int((task.finished_at - contest.start_time).total_seconds()))
        concluded += 1

    if concluded > 0:
        await session.flush()
    return concluded


async def run_task_reaper(
    session_factory: async_sessionmaker[AsyncSession],
    poll_interval_seconds: int,
    stop_event: asyncio.Event,
    logger: logging.Logger,
) -> None:
    """Run the periodic task reaper loop until shutdown is requested.

    Args:
        session_factory: Async session factory for database access.
        poll_interval_seconds: How often (in seconds) to run the reaper cycle.
        stop_event: Event that signals the loop to stop gracefully.
        logger: Logger instance for reaper activity.
    """

    async def _cycle(session: AsyncSession) -> None:
        released = await release_expired_tasks(session)
        concluded = await conclude_finished_contest_tasks(session)
        if released > 0:
            logger.info("Task reaper released %s stale task(s)", released)
        if concluded > 0:
            logger.info("Task reaper auto-concluded %s task(s) for ended contests", concluded)

    await run_reaper_loop(
        session_factory,
        poll_interval_seconds,
        stop_event,
        logger,
        collect_message="Collecting stale tasks...",
        failure_message="Task reaper cycle failed",
        cycle=_cycle,
    )
