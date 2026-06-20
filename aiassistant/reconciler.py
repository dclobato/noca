#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reconciler loop for AI review jobs lost between Postgres and Valkey.

The request route commits ``arena_submissions.submit_to_ai=True`` and then, as a
separate non-transactional step, enqueues the job on Valkey. A crash or failure
between those two writes leaves a submission flagged for review with no job on
the queue — invisible to the inflight reaper. This loop is the database-driven
safety net: it periodically finds such stuck submissions and re-enqueues them.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from aiassistant.db.queries import StuckAIReviewSubmission, get_stuck_ai_review_submissions
from shared.queue_schema import ArenaAIReviewJob
from shared.services.valkey_service import ValkeyRuntime
from shared.services.valkey_service.queue_ops import (
    enqueue_arena_ai_review_job,
    get_ai_review_queued_ids,
)


async def run_reconciler_loop(
    engine: AsyncEngine,
    valkey_runtime: ValkeyRuntime,
    stop_event: asyncio.Event,
    logger: logging.Logger,
    reconciler_interval_s: float,
    grace_s: float,
    batch_size: int,
) -> None:
    """Periodically re-enqueue AI review jobs that were lost after commit.

    Each cycle queries the database for submissions flagged ``submit_to_ai`` that
    are older than ``grace_s`` seconds, have no review row, and have no active
    batch job. It then reads the live Valkey pending/inflight sets once and
    re-enqueues only the stuck submissions that have no queue presence.

    Args:
        engine: Async SQLAlchemy engine for database access.
        valkey_runtime: Connected Valkey runtime for queue operations.
        stop_event: Event set by the main worker to request shutdown.
        logger: Module-level logger.
        reconciler_interval_s: Seconds between consecutive reconciliation sweeps.
        grace_s: Minimum age (seconds since last update) before a flagged
            submission is eligible, to avoid racing a fresh request's enqueue.
        batch_size: Maximum submissions to re-enqueue per sweep.
    """
    logger.info(
        "Reconciler started (interval=%.0fs, grace=%.0fs, batch_size=%d)",
        reconciler_interval_s,
        grace_s,
        batch_size,
    )

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=reconciler_interval_s)
            break  # stop_event was set during sleep
        except TimeoutError:
            pass

        if stop_event.is_set():
            break

        try:
            await _reconcile_once(
                engine=engine,
                valkey_runtime=valkey_runtime,
                logger=logger,
                grace_s=grace_s,
                batch_size=batch_size,
            )
        except Exception:
            logger.exception("Reconciler sweep failed")

    logger.info("Reconciler stopped")


async def _reconcile_once(
    engine: AsyncEngine,
    valkey_runtime: ValkeyRuntime,
    logger: logging.Logger,
    grace_s: float,
    batch_size: int,
) -> None:
    """Run a single reconciliation sweep.

    Args:
        engine: Async SQLAlchemy engine for database access.
        valkey_runtime: Connected Valkey runtime.
        logger: Module-level logger.
        grace_s: Minimum age in seconds before a flagged submission is eligible.
        batch_size: Maximum submissions to re-enqueue this sweep.
    """
    older_than = datetime.now(UTC) - timedelta(seconds=grace_s)
    async with engine.begin() as conn:
        candidates = await get_stuck_ai_review_submissions(conn, older_than, batch_size)

    if not candidates:
        return

    queued_ids = await get_ai_review_queued_ids(valkey_runtime)
    lost = [c for c in candidates if c.submission_id not in queued_ids]

    if not lost:
        logger.debug("Reconciler: %d flagged submission(s) all have live queue presence", len(candidates))
        return

    logger.warning("Reconciler found %d AI review job(s) lost after commit; re-enqueuing", len(lost))
    for submission in lost:
        try:
            await _reenqueue(valkey_runtime, submission, logger)
        except Exception:
            logger.exception("Reconciler failed to re-enqueue submission %s", submission.submission_id)


async def _reenqueue(
    valkey_runtime: ValkeyRuntime,
    submission: StuckAIReviewSubmission,
    logger: logging.Logger,
) -> None:
    """Rebuild and enqueue an AI review job for a lost submission.

    Args:
        valkey_runtime: Connected Valkey runtime.
        submission: Stuck submission metadata from the database.
        logger: Module-level logger.
    """
    job = ArenaAIReviewJob(
        submission_id=submission.submission_id,
        user_id=submission.user_id,
        problem_id=submission.problem_id,
        language_id=submission.language_id,
        use_platform_key=submission.use_platform_key,
    )
    await enqueue_arena_ai_review_job(valkey_runtime, job)
    logger.info(
        "Reconciler re-enqueued lost AI review job %s (use_platform_key=%s)",
        submission.submission_id,
        submission.use_platform_key,
    )
