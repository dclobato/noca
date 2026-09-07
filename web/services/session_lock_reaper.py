#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Releases the IP bindings of contests that have ended.

The single-session policy stops applying at the end instant, so a binding left
on a finished contest is already inert and nothing needs cleaning up for
correctness *today*. It matters for the contest's next life: `start_time` is
editable, so a contest that is rescheduled -- a practice round re-run next week,
a mirror sitting of the same event -- starts enforcing again against addresses
recorded at the previous sitting, and every team is refused from a seat it never
sat in. This loop is what keeps that from happening.

Two properties are deliberate.

**It clears the binding but does not touch `session_epoch`.** The release an
administrator performs mid-contest bumps it, because the sessions bound to the
old address must be superseded or the first of them re-binds it. After the end
there is nothing to supersede: the policy no longer governs those sessions, and
bumping would sign out every team of a finished contest while they are still
reading their runs and the final scoreboard.

**It works per contest, not per user.** The bound-user population of a large
contest is every team it has, so the cycle asks which *contests* still hold a
binding -- a set the size of the contest table at worst -- keeps the ones that
have ended, and releases each in one statement. The cost per cycle is therefore
flat in the number of teams.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.db_schema import users as users_t
from web.models.contest import Contest
from web.services.reaper_runner import run_reaper_loop


async def release_ended_contest_ip_locks(session: AsyncSession) -> int:
    """Clear `locked_ip` and `locked_at` for every user of an ended contest.

    Args:
        session: Database session. The caller commits, as the reaper runner
            does after each cycle.

    Returns:
        How many user rows were released.

    Notes:
        There is deliberately no ``now`` parameter, unlike the sibling reapers:
        "ended" is `Contest.is_past`, which reads the clock itself, and one
        definition of it governs the login, the per-request check and this loop.
        An injectable clock here would be a second answer free to disagree with
        that one, and the `is_past` filter runs in Python for the same reason
        rather than being reproduced as a SQL expression.
    """
    bound_contest_ids = (
        (await session.execute(select(users_t.c.contest_id).where(users_t.c.locked_ip.is_not(None)).distinct()))
        .scalars()
        .all()
    )
    if not bound_contest_ids:
        return 0

    contests = (await session.execute(select(Contest).where(Contest.id.in_(bound_contest_ids)))).scalars().all()
    ended_ids = [contest.id for contest in contests if contest.is_past]
    if not ended_ids:
        return 0

    statement = (
        update(users_t)
        .where(users_t.c.contest_id.in_(ended_ids), users_t.c.locked_ip.is_not(None))
        .values(locked_ip=None, locked_at=None)
    )
    result = cast(CursorResult[Any], await session.execute(statement))
    return int(result.rowcount or 0)


async def run_session_lock_reaper(
    session_factory: async_sessionmaker[AsyncSession],
    poll_interval_seconds: int,
    stop_event: asyncio.Event,
    logger: logging.Logger,
) -> None:
    """Run the periodic session-lock reaper loop until shutdown is requested.

    Args:
        session_factory: Factory the runner opens one session per cycle from.
        poll_interval_seconds: How long to sleep between cycles.
        stop_event: Set to end the loop at the next opportunity.
        logger: Logger for cycle outcomes and failures.
    """

    async def _cycle(session: AsyncSession) -> None:
        released = await release_ended_contest_ip_locks(session)
        if released > 0:
            logger.info("Session-lock reaper released %s IP binding(s) from ended contests", released)

    await run_reaper_loop(
        session_factory,
        poll_interval_seconds,
        stop_event,
        logger,
        collect_message="Collecting IP bindings held by ended contests...",
        failure_message="Session-lock reaper cycle failed",
        cycle=_cycle,
    )
