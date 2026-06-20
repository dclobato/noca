#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def run_reaper_loop(
    session_factory: async_sessionmaker[AsyncSession],
    poll_interval_seconds: int,
    stop_event: asyncio.Event,
    logger: logging.Logger,
    *,
    collect_message: str,
    failure_message: str,
    cycle: Callable[[AsyncSession], Awaitable[None]],
) -> None:
    """Run a generic periodic reaper loop until shutdown is requested.

    Args:
        session_factory: Async session factory for database access.
        poll_interval_seconds: How often (in seconds) to run each cycle.
        stop_event: Event that signals graceful shutdown.
        logger: Logger instance used by the caller.
        collect_message: Message logged at the beginning of each cycle.
        failure_message: Message used when a cycle raises unexpectedly.
        cycle: Async callback that performs one reaper cycle.
    """
    while not stop_event.is_set():
        try:
            logger.info(collect_message)
            async with session_factory() as session:
                await cycle(session)
                await session.commit()
        except Exception:
            logger.exception(failure_message)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_seconds)
        except TimeoutError:
            continue
