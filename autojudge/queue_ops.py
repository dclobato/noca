#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Valkey/Redis queue operations for the judge worker.

Queue key protocol
------------------
  priority list:  judge:queue:priority   (LPUSH by API, live contest)
  profiling list: judge:queue:profiling  (LPUSH by API, Auto-Limit)
  pending list:   judge:queue:pending    (LPUSH by API, normal priority)
  inflight list:  judge:queue:inflight   (LMOVE → here by worker)

The worker prefers profiling, then priority, then pending.
"""

import asyncio
import logging
from typing import Any, cast

import valkey.asyncio as aiovalkey

from autojudge.config import settings
from shared.queue_schema import ArenaVerdictEvent, JobKind, VerdictEvent
from shared.services.valkey_service import _publish_arena_verdict_with_client

logger = logging.getLogger(__name__)

Valkey_Client = aiovalkey.Valkey

_DEQUEUE_READY_JOB_SCRIPT = """
for i = 1, #KEYS - 1 do
    local job_id = redis.call("RPOP", KEYS[i])
    if job_id then
        redis.call("LPUSH", KEYS[#KEYS], job_id)
        return job_id
    end
end
return nil
"""


async def dequeue_job_id(valkey: Valkey_Client) -> str | None:
    """
    Atomically move one job id from the profiling, priority, or pending queue into inflight.

    Profiling queue is checked first, then priority, then normal pending queue.

    Args:
        valkey: Async Valkey client.

    Returns:
        Job ID string, or None if no jobs are available.
    """
    ready_result = valkey.eval(
        _DEQUEUE_READY_JOB_SCRIPT,
        4,
        settings.queue_profiling_key,
        settings.queue_priority_key,
        settings.queue_pending_key,
        settings.queue_inflight_key,
    )
    if asyncio.iscoroutine(ready_result):
        ready_result = await ready_result
    if ready_result is not None:
        return ready_result.decode() if isinstance(ready_result, bytes) else cast(str, ready_result)
    return None


async def remove_from_inflight(valkey: Valkey_Client, job_id: str) -> None:
    """
    Remove a job id from the inflight list and dispatch-time sorted set (best-effort).

    Args:
        valkey: Async Valkey client.
        job_id: Job ID to remove.
    """
    try:
        pipe = valkey.pipeline()
        pipe.lrem(settings.queue_inflight_key, 1, job_id)
        pipe.zrem(settings.queue_inflight_times_key, job_id)
        await pipe.execute()
    except Exception as exc:
        logger.error(f"Failed to remove job id {job_id} from inflight list: {str(exc)}")


async def get_job_kind(valkey: Valkey_Client, job_id: str) -> str:
    """
    Return the stored job kind for a job, defaulting legacy hashes to submission.

    Args:
        valkey: Async Valkey client.
        job_id: Job ID to look up.

    Returns:
        JobKind string.
    """
    raw_kind = await cast(
        Any,
        valkey.hget(f"{settings.queue_job_hash_prefix}:{job_id}", "job_kind"),
    )
    if raw_kind is None:
        return JobKind.SUBMISSION
    return raw_kind.decode() if isinstance(raw_kind, bytes) else str(raw_kind)


async def publish_verdict(valkey: Valkey_Client, event: VerdictEvent) -> None:
    """
    Publish a VerdictEvent to the Redis pub/sub results channel.

    Args:
        valkey: Async Valkey client.
        event: Verdict event to publish.
    """
    try:
        await valkey.publish(settings.queue_results_channel, event.model_dump_json())
    except Exception as exc:
        logger.error(
            f"Failed to publish verdict event '{event.verdict}' for submission {event.submission_id}: {str(exc)}"
        )


async def publish_arena_verdict(valkey: Valkey_Client, event: ArenaVerdictEvent) -> None:
    """
    Publish an ArenaVerdictEvent to the Arena pub/sub results channel.

    Delegates to the shared ``publish_arena_verdict_with_client`` helper so the channel
    name has a single source of truth (the shared ``ARENA_RESULTS_CHANNEL`` constant) and
    cannot drift from the Arena HTTP subscriber.

    Args:
        valkey: Async Valkey client.
        event: Arena verdict event to publish.
    """
    try:
        await _publish_arena_verdict_with_client(valkey, event)
    except Exception as exc:
        logger.error(
            f"Failed to publish Arena verdict event '{event.verdict}' for submission {event.submission_id}: {str(exc)}"
        )
