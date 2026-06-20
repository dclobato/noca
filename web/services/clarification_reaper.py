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

from shared.timing import compute_timestamp_seconds
from web.models._base import _utcnow
from web.models.clarification import Clarification
from web.models.contest import Contest
from web.models.problem import Problem
from web.services.reaper_runner import run_reaper_loop

AUTO_ANSWER_PLACEHOLDER = "Contest ended before this clarification was answered."


async def conclude_finished_contest_clarifications(
    session: AsyncSession,
    now: datetime.datetime | None = None,
) -> int:
    """Auto-answer still-open clarifications for contests that have already ended."""
    current_time = now or _utcnow()
    result = await session.execute(
        select(Clarification, Contest)
        .join(Problem, Clarification.problem_id == Problem.id)
        .join(Contest, Problem.contest_id == Contest.id)
        .where(
            Clarification.answered_at.is_(None),
            Clarification.hidden.is_(False),
        )
    )

    concluded = 0
    for clarification, contest in result.all():
        if not contest.is_past:
            continue

        clarification.answer = AUTO_ANSWER_PLACEHOLDER
        clarification.answered_at = current_time
        clarification.answered_timestamp_seconds = compute_timestamp_seconds(contest.start_time, current_time)
        clarification.judge_id = contest.owner_user_id
        concluded += 1

    if concluded > 0:
        await session.flush()
    return concluded


async def run_clarification_reaper(
    session_factory: async_sessionmaker[AsyncSession],
    poll_interval_seconds: int,
    stop_event: asyncio.Event,
    logger: logging.Logger,
) -> None:
    """Run the periodic clarification reaper loop until shutdown is requested."""

    async def _cycle(session: AsyncSession) -> None:
        concluded = await conclude_finished_contest_clarifications(session)
        if concluded > 0:
            logger.info("Clarification reaper auto-answered %s clarification(s) for ended contests", concluded)

    await run_reaper_loop(
        session_factory,
        poll_interval_seconds,
        stop_event,
        logger,
        collect_message="Collecting open clarifications for ended contests...",
        failure_message="Clarification reaper cycle failed",
        cycle=_cycle,
    )
