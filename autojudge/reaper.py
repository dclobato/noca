#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Stale in-flight job recovery loop.

Problem
-------
When a worker process is killed (OOM, SIGKILL, node crash) mid-job, the
judgment_id remains in the inflight list and the judgment row stays in
DISPATCHED or JUDGING status forever. No verdict is ever published. The
contestant sees a spinner that never resolves.

Solution
--------
The reaper runs as a coroutine alongside the worker slots. Every
`reaper_interval_s` seconds it looks for jobs that have been in-flight
longer than `reaper_stale_threshold_minutes` and requeues them.

Data structures
---------------
  judge:queue:inflight       LIST   — judgment IDs currently being judged
  judge:queue:inflight:times ZSET   — score=epoch_s, member=judgment_id
  judge:job:<id>             HASH   — lightweight retry metadata
  judge:queue:pending        LIST   — requeue destination for stale jobs
                                      (never priority, to avoid starving live submissions)
  judge:queue:priority       LIST   — live submissions only; never written by the reaper

Algorithm (per reaper cycle)
----------------------------
  1. ZRANGEBYSCORE inflight:times  0  (now - threshold)
     → list of stale judgment_ids
  2. For each candidate, one Lua script revalidates that its current score is
     still stale and that the id remains inflight, then either cleans finished
     metadata, records an exhaustion tombstone, or requeues it.

Multi-worker safety
-------------------
Multiple worker processes may run reapers simultaneously. Candidate discovery
is intentionally advisory: the Lua transition rechecks both the sorted-set score
and inflight membership at action time. Only one reaper can move the job, and a
candidate whose score was refreshed for a new attempt is left untouched.

Distinguishing the reaper's requeue from a live enqueue
-------------------------------------------------------
Requeued jobs go to the NORMAL (pending) queue, never the priority queue.
This ensures live contest submissions are never delayed by a storm of
requeued stale jobs. The priority queue is reserved for the API's
`is_rejudge=False` live submissions.
"""

import asyncio
import json
import logging
import time

from autojudge.config import settings
from autojudge.metrics import (
    REAPER_ALREADY_DONE_TOTAL,
    REAPER_CYCLES_TOTAL,
    REAPER_DROPPED_TOTAL,
    REAPER_ERRORS_TOTAL,
    REAPER_REQUEUED_TOTAL,
)
from autojudge.queue_ops import Valkey_Client, reap_stale_job

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point (imported by worker.py)
# ---------------------------------------------------------------------------


async def reaper_loop(
    shutdown_event: asyncio.Event,
    redis: Valkey_Client,
) -> None:
    """
    Run the stale-job reaper until shutdown_event is set.

    Sleeps for reaper_interval_s between scans. A short initial delay gives
    the worker slots time to warm their pools before the first scan fires,
    avoiding false positives on freshly-started jobs.
    """
    logger.info("Reaper started")
    logger.info(
        json.dumps(
            {
                "interval_s": settings.REAPER_INTERVAL_S,
                "stale_threshold_min": settings.REAPER_STALE_THRESHOLD_MINUTES,
                "max_requeue": settings.REAPER_MAX_REQUEUE_COUNT,
            },
            indent=2,
        )
    )

    # Initial delay: let workers settle before the first scan fires.
    # Use 1-second increments so shutdown_event is checked throughout.
    initial_deadline = time.monotonic() + settings.REAPER_INTERVAL_S
    while not shutdown_event.is_set() and time.monotonic() < initial_deadline:
        await asyncio.sleep(1.0)

    while not shutdown_event.is_set():
        try:
            requeued, dropped, already_done = await _reaper_cycle(redis)
            REAPER_CYCLES_TOTAL.inc()
            REAPER_REQUEUED_TOTAL.inc(requeued)
            REAPER_DROPPED_TOTAL.inc(dropped)
            REAPER_ALREADY_DONE_TOTAL.inc(already_done)
            if requeued or dropped or already_done:
                logger.info("Reaper cycle complete")
                logger.info(
                    json.dumps(
                        {
                            "requeued": requeued,
                            "dropped": dropped,
                            "already_done": already_done,
                        },
                        indent=2,
                    )
                )
        except OSError as exc:
            REAPER_ERRORS_TOTAL.inc()
            logger.warning("Reaper cycle skipped — DB unreachable: %s", exc)
        except Exception as exc:
            REAPER_ERRORS_TOTAL.inc()
            logger.error(
                "Reaper cycle failed — will retry next interval: %s",
                exc,
                exc_info=True,
            )

        # Sleep in small increments so shutdown is responsive
        deadline = time.monotonic() + settings.REAPER_INTERVAL_S
        while not shutdown_event.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(1.0)

    logger.info("Reaper stopped")


# ---------------------------------------------------------------------------
# Single reaper cycle
# ---------------------------------------------------------------------------


async def _reaper_cycle(valkey: Valkey_Client) -> tuple[int, int, int]:
    """
    One scan of the inflight sorted set. Returns (requeued, dropped, already_done).

    Parameters
    ----------
    valkey : aiovalkey.Valkey

    Returns
    -------
    requeued : int
        Jobs moved back to the pending queue.
    dropped : int
        Jobs abandoned because they exceeded max_requeue_count.
    already_done : int
        Jobs found in the sorted set but whose hash no longer exists
        (finished normally — stale timestamp entry cleaned up).
    """
    threshold_s = settings.REAPER_STALE_THRESHOLD_MINUTES * 60.0
    cutoff_epoch = time.time() - threshold_s

    # Find all judgment_ids dispatched before the cutoff
    stale_ids = await valkey.zrangebyscore(
        settings.queue_inflight_times_key,
        min=0,
        max=cutoff_epoch,
    )

    if not stale_ids:
        return 0, 0, 0

    requeued = 0
    dropped = 0
    already_done = 0

    for raw_id in stale_ids:
        judgment_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
        outcome, _job_kind, requeue_count = await reap_stale_job(
            valkey,
            job_id=judgment_id,
            cutoff_epoch=cutoff_epoch,
            max_requeue_count=settings.REAPER_MAX_REQUEUE_COUNT,
        )
        if outcome in {"not_stale", "not_inflight"}:
            continue
        if outcome == "already_done":
            already_done += 1
            continue
        if outcome == "dropped":
            dropped += 1
            logger.error("Reaper: job exceeded max requeue count — dropped")
            logger.error(
                json.dumps(
                    {
                        "judgment_id": judgment_id,
                        "requeue_count": requeue_count,
                        "max": settings.REAPER_MAX_REQUEUE_COUNT,
                    },
                    indent=2,
                )
            )
            continue

        requeued += 1
        logger.warning("Reaper: stale job requeued")
        logger.error(
            json.dumps(
                {
                    "judgment_id": judgment_id,
                    "requeue_count": requeue_count,
                    "stale_after_min": settings.REAPER_STALE_THRESHOLD_MINUTES,
                },
                indent=2,
            )
        )

    return requeued, dropped, already_done
