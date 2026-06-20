#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reaper loop for stale Arena AI review inflight jobs.

Periodically scans the ``ai:queue:inflight:times`` ZSET for jobs that have
been inflight longer than the configured stale threshold. Stale jobs are
re-enqueued with an incremented ``requeue_count``; jobs that exceed the
maximum requeue limit are discarded.
"""

from __future__ import annotations

import asyncio
import logging

from shared.queue_schema import ArenaAIReviewJob
from shared.services.valkey_service import ValkeyRuntime
from shared.services.valkey_service.constants import QUEUE_AI_REVIEW_JOB_HASH_PREFIX
from shared.services.valkey_service.queue_ops import (
    complete_arena_ai_review_job,
    enqueue_arena_ai_review_job,
    get_ai_review_job_hash,
    get_stale_ai_review_job_ids,
    remove_from_ai_review_inflight,
)


async def run_reaper_loop(
    valkey_runtime: ValkeyRuntime,
    stop_event: asyncio.Event,
    logger: logging.Logger,
    stale_threshold_s: float,
    reaper_interval_s: float,
    max_requeue_count: int,
) -> None:
    """Periodically detect and recover stale AI review inflight jobs.

    The reaper sleeps for ``reaper_interval_s`` seconds between scans, then
    fetches all submission_ids whose dispatch timestamp in the ZSET is older
    than ``stale_threshold_s`` seconds. For each stale job:

    - If the job hash is missing from Valkey (already processed), only the
      inflight entry is cleaned up.
    - If ``requeue_count`` would exceed ``max_requeue_count``, the job is
      discarded with a warning.
    - Otherwise the job is removed from inflight and re-enqueued with an
      incremented ``requeue_count``.

    Args:
        valkey_runtime: Connected Valkey runtime for queue operations.
        stop_event: Event set by the main worker to request shutdown.
        logger: Module-level logger.
        stale_threshold_s: Age in seconds after which an inflight job is stale.
        reaper_interval_s: Seconds between consecutive reaper scans.
        max_requeue_count: Maximum number of times a job may be re-enqueued.
    """
    logger.info(
        "Reaper started (stale_threshold=%.0fs, interval=%.0fs, max_requeue=%d)",
        stale_threshold_s,
        reaper_interval_s,
        max_requeue_count,
    )

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=reaper_interval_s)
            break  # stop_event was set during sleep
        except TimeoutError:
            pass

        if stop_event.is_set():
            break

        try:
            stale_ids = await get_stale_ai_review_job_ids(valkey_runtime, stale_threshold_s)
        except Exception:
            logger.exception("Reaper failed to fetch stale AI review job ids")
            continue

        if not stale_ids:
            continue

        logger.info("Reaper found %d stale AI review job(s)", len(stale_ids))

        for submission_id in stale_ids:
            try:
                await _handle_stale_job(
                    valkey_runtime=valkey_runtime,
                    submission_id=submission_id,
                    max_requeue_count=max_requeue_count,
                    logger=logger,
                )
            except Exception:
                logger.exception("Reaper failed to handle stale job %s", submission_id)

    logger.info("Reaper stopped")


async def _handle_stale_job(
    valkey_runtime: ValkeyRuntime,
    submission_id: str,
    max_requeue_count: int,
    logger: logging.Logger,
) -> None:
    """Process a single stale inflight job.

    Args:
        valkey_runtime: Connected Valkey runtime.
        submission_id: Submission UUID of the stale inflight job.
        max_requeue_count: Discard threshold for requeue_count.
        logger: Module-level logger.
    """
    job_hash = await get_ai_review_job_hash(valkey_runtime, submission_id)

    if job_hash is None:
        # Hash already cleaned up — the worker completed and removed inflight
        # after we read the ZSET but before we fetched the hash. Just clean up.
        logger.debug("Stale job %s has no hash; removing inflight entry", submission_id)
        await remove_from_ai_review_inflight(valkey_runtime, submission_id)
        return

    requeue_count = int(job_hash.get("requeue_count", 0)) + 1

    if requeue_count > max_requeue_count:
        logger.warning(
            "Discarding stale AI review job %s — requeue_count %d exceeds max %d",
            submission_id,
            requeue_count,
            max_requeue_count,
        )
        await complete_arena_ai_review_job(valkey_runtime, submission_id)
        return

    # Remove from inflight before requeueing to avoid double-processing.
    await remove_from_ai_review_inflight(valkey_runtime, submission_id)

    has_frozen_key_choice = "use_platform_key" in job_hash
    use_platform_key = job_hash.get("use_platform_key", "false").lower() == "true"
    new_job = ArenaAIReviewJob(
        submission_id=submission_id,
        user_id=job_hash["user_id"],
        problem_id=job_hash["problem_id"],
        language_id=job_hash["language_id"],
        use_platform_key=use_platform_key,
        requeue_count=requeue_count,
    )
    await enqueue_arena_ai_review_job(valkey_runtime, new_job)
    if not has_frozen_key_choice:
        await valkey_runtime.hdel(f"{QUEUE_AI_REVIEW_JOB_HASH_PREFIX}:{submission_id}", "use_platform_key")
    logger.info(
        "Requeued stale AI review job %s (requeue_count=%d)",
        submission_id,
        requeue_count,
    )
