#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Periodic rating recomputation loops.

Three background coroutines form a sequential chain so dependent ratings always
reflect the freshest inputs:

  - run_problem_rating_loop     — rates all problems, then signals user loop.
  - run_user_rating_loop        — waits for problem signal; signals affiliation loop.
  - run_affiliation_rating_loop — waits for user signal before running each cycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.services.arena_heatmap import compute_all_user_heatmaps
from shared.services.arena_rating import (
    NextRatingUpdateCallback,
    _publish_next_rating_update,
    rate_all_affiliations,
    rate_all_problems,
    rate_all_users,
)
from shared.services.arena_stats import compute_all_problem_statistics


async def run_problem_rating_loop(
    session_factory: async_sessionmaker[AsyncSession],
    interval_seconds: int,
    stop_event: asyncio.Event,
    logger: logging.Logger,
    problem_done: asyncio.Event,
    *,
    run_immediately: bool = False,
    next_update_callback: NextRatingUpdateCallback | None = None,
    on_cycle_start: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Run the problem rating cycle periodically until shutdown is requested.

    After each successful cycle, sets ``problem_done`` to unblock the user
    rating loop. On failure, ``problem_done`` is **not** set so user scores
    are never recomputed from stale problem ratings.

    Args:
        session_factory: Async session factory for database access.
        interval_seconds: Seconds between rating cycles.
        stop_event: Setting this event triggers graceful shutdown.
        logger: Logger instance for cycle reporting.
        problem_done: Event shared with the user rating loop; set on success.
        run_immediately: When True, skip the initial interval wait and run on
            the first iteration. Useful for ``COMPUTE_RATINGS_ON_STARTUP=true``.
        next_update_callback: Optional callback receiving the next scheduled
            problem-rating cycle timestamp, or ``None`` while no deadline exists.
        on_cycle_start: Optional async callback invoked at the start of each
            cycle. Exceptions are caught and logged so a failed callback never
            stops the rating loop.
    """
    if not run_immediately:
        _publish_next_rating_update(
            next_update_callback,
            datetime.now(UTC) + timedelta(seconds=interval_seconds),
        )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)

    while not stop_event.is_set():
        if on_cycle_start is not None:
            try:
                await on_cycle_start()
            except Exception as exc:
                logger.warning("Failed to publish last-job timestamp: %s", exc)
        _publish_next_rating_update(next_update_callback, None)
        success = False
        try:
            async with session_factory() as session:
                count = await rate_all_problems(session)
                await session.commit()
            logger.info("Problem rating cycle complete (%d problems updated)", count)
            success = True
        except OSError as exc:
            logger.warning("Problem rating cycle skipped — DB unreachable: %s", exc)
        except Exception:
            logger.exception("Problem rating cycle failed")

        if success:
            problem_done.set()

        if stop_event.is_set():
            break

        _publish_next_rating_update(
            next_update_callback,
            datetime.now(UTC) + timedelta(seconds=interval_seconds),
        )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue

    _publish_next_rating_update(next_update_callback, None)


async def run_user_rating_loop(
    session_factory: async_sessionmaker[AsyncSession],
    interval_seconds: int,
    stop_event: asyncio.Event,
    logger: logging.Logger,
    problem_done: asyncio.Event,
    user_done: asyncio.Event,
) -> None:
    """Run the user rating cycle after each successful problem rating cycle.

    Blocks on ``problem_done`` before each run, ensuring user scores always
    reflect freshly computed problem difficulties. On success, sets ``user_done``
    to unblock the affiliation rating loop.

    Args:
        session_factory: Async session factory for database access.
        interval_seconds: Passed for symmetry; the effective cadence is driven
            by ``problem_done`` signals from the problem rating loop.
        stop_event: Setting this event triggers graceful shutdown.
        logger: Logger instance for cycle reporting.
        problem_done: Event set by the problem rating loop on success.
        user_done: Event to set after a successful user rating cycle; signals
            the affiliation rating loop to proceed.
    """
    while not stop_event.is_set():
        await problem_done.wait()
        if stop_event.is_set():
            break
        success = False
        try:
            async with session_factory() as session:
                count = await rate_all_users(session)
                await session.commit()
            logger.info("User rating cycle complete (%d users updated)", count)
            success = True
        except OSError as exc:
            logger.warning("User rating cycle skipped — DB unreachable: %s", exc)
        except Exception:
            logger.exception("User rating cycle failed")
        if success:
            try:
                async with session_factory() as session:
                    hcount = await compute_all_user_heatmaps(session)
                    await session.commit()
                logger.info("Submission heatmap cycle complete (%d users)", hcount)
            except OSError as exc:
                logger.warning("Heatmap cycle skipped — DB unreachable: %s", exc)
            except Exception:
                logger.exception("Heatmap cycle failed")
        problem_done.clear()
        if success:
            user_done.set()


async def run_affiliation_rating_loop(
    session_factory: async_sessionmaker[AsyncSession],
    stop_event: asyncio.Event,
    logger: logging.Logger,
    user_done: asyncio.Event,
    f: float,
) -> None:
    """Run the affiliation rating cycle after each successful user rating cycle.

    Blocks on ``user_done`` before each run, ensuring affiliation scores always
    reflect the freshest user ratings. Clears ``user_done`` after each cycle
    regardless of success so the loop does not re-run on stale signals.

    Args:
        session_factory: Async session factory for database access.
        stop_event: Setting this event triggers graceful shutdown.
        logger: Logger instance for cycle reporting.
        user_done: Event set by the user rating loop on success.
        f: Geometric decay factor forwarded to ``rate_all_affiliations``.
    """
    while not stop_event.is_set():
        await user_done.wait()
        if stop_event.is_set():
            break
        try:
            async with session_factory() as session:
                count = await rate_all_affiliations(session, f)
                await session.commit()
            logger.info("Affiliation rating cycle complete (%d affiliations updated)", count)
        except OSError as exc:
            logger.warning("Affiliation rating cycle skipped — DB unreachable: %s", exc)
        except Exception:
            logger.exception("Affiliation rating cycle failed")
        user_done.clear()


async def run_problem_stats_loop(
    session_factory: async_sessionmaker[AsyncSession],
    interval_seconds: int,
    stop_event: asyncio.Event,
    logger: logging.Logger,
    *,
    run_immediately: bool = False,
) -> None:
    """Recompute per-problem statistics snapshots on a fixed interval.

    Independent of the problem/user/affiliation rating chain: it runs on its own
    timer (``STATS_INTERVAL``) and writes the ``arena_problem_statistics`` table
    read by the Arena statistics page. Failures are logged and the loop keeps
    running so a transient error does not stop future cycles.

    Args:
        session_factory: Async session factory for database access.
        interval_seconds: Seconds between statistics cycles.
        stop_event: Setting this event triggers graceful shutdown.
        logger: Logger instance for cycle reporting.
        run_immediately: When True, skip the initial interval wait and run on the
            first iteration.
    """
    if not run_immediately:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)

    while not stop_event.is_set():
        try:
            async with session_factory() as session:
                count = await compute_all_problem_statistics(session)
                await session.commit()
            logger.info("Problem statistics cycle complete (%d problems updated)", count)
        except OSError as exc:
            logger.warning("Problem statistics cycle skipped — DB unreachable: %s", exc)
        except Exception:
            logger.exception("Problem statistics cycle failed")

        if stop_event.is_set():
            break
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue
