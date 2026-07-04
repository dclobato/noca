#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared periodic retention cleanup for the security-event log.

Both the Web and Arena HTTP processes run this loop over their own module
ownership set, so an independently deployed Web-only or Arena-only site still
prunes exactly the events it produces. The loop is self-contained (no imports
from ``web`` or ``arena``) so it can live on the shared side of the module
boundary.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.services.security_events import delete_security_events_older_than


async def run_security_events_reaper(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    poll_interval_seconds: int,
    retention_days: int,
    modules: Sequence[str],
    stop_event: asyncio.Event,
    logger: logging.Logger,
) -> None:
    """Run the periodic security-event retention cleanup until shutdown.

    Runs one cycle immediately, then repeats every ``poll_interval_seconds``
    until ``stop_event`` is set. Each cycle deletes rows older than
    ``retention_days`` whose ``module`` is in ``modules``.

    Args:
        session_factory: Async session factory for database access.
        poll_interval_seconds: How often (in seconds) to run each cycle.
        retention_days: Age threshold in days for deleting old events.
        modules: The ``module`` values this runtime is responsible for pruning.
        stop_event: Event that signals the loop to stop gracefully.
        logger: Logger instance for reaper activity.
    """
    module_label = ",".join(modules)
    while not stop_event.is_set():
        try:
            async with session_factory() as session:
                deleted = await delete_security_events_older_than(
                    session,
                    retention_days=retention_days,
                    modules=modules,
                )
                await session.commit()
            if deleted > 0:
                logger.info(
                    "Security-events reaper deleted %s event(s) older than %s day(s) for module(s) %s",
                    deleted,
                    retention_days,
                    module_label,
                )
        except Exception:
            logger.exception("Security-events reaper cycle failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_seconds)
        except TimeoutError:
            continue
