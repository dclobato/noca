#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reaper loop for stale mail inflight jobs.

Periodically scans ``mail:queue:inflight:times`` for jobs that have been
inflight longer than the stale threshold. That covers two cases the worker
deliberately leaves behind: a crash mid-delivery, and a provider refusal --
the worker leaves a refused job inflight rather than retrying at once, so the
stale threshold doubles as the retry delay.

Each stale job is handled by **one atomic Valkey script**
(``requeue_stale_mail_job``): it is requeued with an incremented
``requeue_count`` only while it is still inflight and its hash still exists,
so a crash between "remove" and "push" cannot lose it and a completion racing
with the reaper cannot resurrect a delivered message. The hash keeps its
original TTL -- a retry never extends how long a credential sits at rest.
Past the requeue cap the job is dropped with a warning.
"""

from __future__ import annotations

import asyncio
import logging

from shared.services.valkey_service import ValkeyRuntime
from shared.services.valkey_service.queue_ops import get_stale_mail_job_ids, requeue_stale_mail_job


async def run_reaper_loop(
    valkey_runtime: ValkeyRuntime,
    stop_event: asyncio.Event,
    logger: logging.Logger,
    stale_threshold_s: float,
    reaper_interval_s: float,
    max_requeue_count: int,
) -> None:
    """Periodically detect and recover stale mail inflight jobs.

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
            break
        except TimeoutError:
            pass

        if stop_event.is_set():
            break

        try:
            stale_ids = await get_stale_mail_job_ids(valkey_runtime, stale_threshold_s)
        except Exception:
            logger.exception("Reaper failed to fetch stale mail job ids")
            continue

        if not stale_ids:
            continue

        logger.info("Reaper found %d stale mail job(s)", len(stale_ids))

        for job_id in stale_ids:
            try:
                await handle_stale_job(
                    valkey_runtime=valkey_runtime,
                    job_id=job_id,
                    max_requeue_count=max_requeue_count,
                    logger=logger,
                )
            except Exception:
                logger.exception("Reaper failed to handle stale mail job %s", job_id)

    logger.info("Reaper stopped")


async def handle_stale_job(
    valkey_runtime: ValkeyRuntime,
    job_id: str,
    max_requeue_count: int,
    logger: logging.Logger,
) -> str:
    """Requeue or drop a single stale inflight job in one atomic step.

    Args:
        valkey_runtime: Connected Valkey runtime.
        job_id: Job id of the stale inflight entry.
        max_requeue_count: Discard threshold for ``requeue_count``.
        logger: Module-level logger.

    Returns:
        The script's verdict: ``requeued``, ``dropped``, ``expired``,
        ``not_inflight`` or ``unavailable``.
    """
    outcome = await requeue_stale_mail_job(valkey_runtime, job_id, max_requeue_count=max_requeue_count)
    if outcome == "requeued":
        logger.info("Requeued stale mail job %s", job_id)
    elif outcome == "dropped":
        logger.warning("Dropped stale mail job %s -- requeue cap %d reached", job_id, max_requeue_count)
    elif outcome == "expired":
        logger.warning("Stale mail job %s had already expired (TTL); removed its inflight entry", job_id)
    elif outcome == "unavailable":
        logger.warning("Could not reap mail job %s: Valkey unavailable; will retry next scan", job_id)
    else:
        logger.debug("Stale mail job %s was completed meanwhile; nothing to do", job_id)
    return outcome
