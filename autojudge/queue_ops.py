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

The worker prefers profiling, then priority, then pending. This module owns the
Lua transitions that coordinate dequeue, reconciliation, stale reaping, and
attempt-owned cleanup so their key ordering and atomicity stay consistent.
"""

import asyncio
import logging
from collections.abc import Mapping
from typing import Any, Literal, cast

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

_RECONCILE_JOB_SCRIPT = """
local jid = ARGV[1]
local now = ARGV[2]
if redis.call("HGET", KEYS[6], "reaper_dropped") == "true" then
    return "tombstoned"
end
if redis.call("LPOS", KEYS[4], jid) then
    if not redis.call("ZSCORE", KEYS[5], jid) then
        redis.call("ZADD", KEYS[5], now, jid)
    end
    return "inflight"
end
redis.call("ZREM", KEYS[5], jid)
if redis.call("EXISTS", KEYS[7]) == 1 then
    return "locked"
end
if redis.call("LPOS", KEYS[1], jid) or redis.call("LPOS", KEYS[2], jid) or redis.call("LPOS", KEYS[3], jid) then
    if redis.call("EXISTS", KEYS[6]) == 1 then
        return "queued"
    end
    redis.call("HSET", KEYS[6], unpack(ARGV, 3))
    return "hash_repaired"
end
redis.call("HSET", KEYS[6], unpack(ARGV, 3))
redis.call("RPUSH", KEYS[8], jid)
return "recovered"
"""

_CLEANUP_CLAIMED_JOB_SCRIPT = """
if redis.call("GET", KEYS[4]) ~= ARGV[2] then
    return "not_owner"
end
redis.call("DEL", KEYS[3])
redis.call("LREM", KEYS[1], 0, ARGV[1])
redis.call("ZREM", KEYS[2], ARGV[1])
redis.call("DEL", KEYS[4])
return "cleaned"
"""

_DELETE_JOB_STATE_SCRIPT = """
for i = 1, 4 do
    redis.call("LREM", KEYS[i], 0, ARGV[1])
end
redis.call("ZREM", KEYS[5], ARGV[1])
redis.call("DEL", KEYS[6])
redis.call("DEL", KEYS[7])
return 1
"""

_REAP_STALE_JOB_SCRIPT = """
local jid = ARGV[1]
local cutoff = tonumber(ARGV[2])
local max_requeues = tonumber(ARGV[3])
local score = redis.call("ZSCORE", KEYS[1], jid)
if not score or tonumber(score) > cutoff then
    return {"not_stale", "", "0"}
end
if not redis.call("LPOS", KEYS[2], jid) then
    redis.call("ZREM", KEYS[1], jid)
    return {"not_inflight", "", "0"}
end
if redis.call("EXISTS", KEYS[3]) == 0 then
    redis.call("LREM", KEYS[2], 0, jid)
    redis.call("ZREM", KEYS[1], jid)
    return {"already_done", "", "0"}
end

local job_kind = redis.call("HGET", KEYS[3], "job_kind") or "submission"
local requeue_count = tonumber(redis.call("HGET", KEYS[3], "requeue_count") or "0") or 0
if requeue_count >= max_requeues then
    if job_kind == "custom_validator_validation" or job_kind == "solution_test" then
        redis.call("HSET", KEYS[3], "requeue_count", requeue_count, "reaper_dropped", "true")
    else
        redis.call("DEL", KEYS[3])
    end
    redis.call("DEL", KEYS[4])
    redis.call("LREM", KEYS[2], 0, jid)
    redis.call("ZREM", KEYS[1], jid)
    return {"dropped", job_kind, tostring(requeue_count)}
end

local new_count = requeue_count + 1
local destination = KEYS[5]
if job_kind == "profiling" or job_kind == "custom_validator_validation" then
    destination = KEYS[6]
end
redis.call("HSET", KEYS[3], "requeue_count", new_count)
redis.call("RPUSH", destination, jid)
redis.call("DEL", KEYS[4])
redis.call("LREM", KEYS[2], 0, jid)
redis.call("ZREM", KEYS[1], jid)
return {"requeued", job_kind, tostring(new_count)}
"""

ReconcileOutcome = Literal["tombstoned", "inflight", "locked", "queued", "hash_repaired", "recovered"]
CleanupOutcome = Literal["cleaned", "not_owner"]
ReaperOutcome = Literal["not_stale", "not_inflight", "already_done", "dropped", "requeued"]


async def _eval(valkey: Valkey_Client, script: str, numkeys: int, *keys_and_args: Any) -> Any:
    """Execute one Lua script against sync-like or native async test clients."""
    result = valkey.eval(script, numkeys, *keys_and_args)
    return await result if asyncio.iscoroutine(result) else result


def _decode_scalar(value: Any) -> str:
    """Decode a required scalar returned by Valkey."""
    return value.decode() if isinstance(value, bytes) else str(value)


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


async def reconcile_job_state(
    valkey: Valkey_Client,
    *,
    job_id: str,
    target_queue_key: str,
    mapping: Mapping[str, Any],
    now: float,
) -> ReconcileOutcome:
    """Atomically inspect current queue state and repair or enqueue one job.

    Args:
        valkey: Async Valkey client.
        job_id: Shared queue and job-hash identifier.
        target_queue_key: Pending or profiling queue used for recovery.
        mapping: Complete job-hash fields used when state must be rebuilt.
        now: Epoch timestamp used only to backfill a missing inflight deadline.

    Returns:
        The applied reconciliation outcome.
    """
    argv: list[str] = [job_id, str(now)]
    for field_name, value in mapping.items():
        argv.extend((field_name, str(value)))
    result = await _eval(
        valkey,
        _RECONCILE_JOB_SCRIPT,
        8,
        settings.queue_pending_key,
        settings.queue_priority_key,
        settings.queue_profiling_key,
        settings.queue_inflight_key,
        settings.queue_inflight_times_key,
        f"{settings.queue_job_hash_prefix}:{job_id}",
        f"judge:lock:{job_id}",
        target_queue_key,
        *argv,
    )
    return cast(ReconcileOutcome, _decode_scalar(result))


async def cleanup_claimed_job(
    valkey: Valkey_Client,
    *,
    job_id: str,
    lock_token: str,
) -> CleanupOutcome:
    """Atomically remove completed state only while this attempt owns the lock.

    Args:
        valkey: Async Valkey client.
        job_id: Shared queue and job-hash identifier.
        lock_token: Attempt-specific token written during lock acquisition.

    Returns:
        ``cleaned`` when the token owned the lock, otherwise ``not_owner``.
    """
    result = await _eval(
        valkey,
        _CLEANUP_CLAIMED_JOB_SCRIPT,
        4,
        settings.queue_inflight_key,
        settings.queue_inflight_times_key,
        f"{settings.queue_job_hash_prefix}:{job_id}",
        f"judge:lock:{job_id}",
        job_id,
        lock_token,
    )
    return cast(CleanupOutcome, _decode_scalar(result))


async def delete_job_state(valkey: Valkey_Client, job_id: str) -> None:
    """Atomically delete every queue, deadline, hash, and lock entry for a job.

    Args:
        valkey: Async Valkey client.
        job_id: Shared queue and job-hash identifier.
    """
    await _eval(
        valkey,
        _DELETE_JOB_STATE_SCRIPT,
        7,
        settings.queue_pending_key,
        settings.queue_priority_key,
        settings.queue_profiling_key,
        settings.queue_inflight_key,
        settings.queue_inflight_times_key,
        f"{settings.queue_job_hash_prefix}:{job_id}",
        f"judge:lock:{job_id}",
        job_id,
    )


async def reap_stale_job(
    valkey: Valkey_Client,
    *,
    job_id: str,
    cutoff_epoch: float,
    max_requeue_count: int,
) -> tuple[ReaperOutcome, str, int]:
    """Atomically revalidate and transition one candidate stale inflight job.

    Args:
        valkey: Async Valkey client.
        job_id: Candidate returned by the advisory stale-score scan.
        cutoff_epoch: Maximum score still considered stale.
        max_requeue_count: Retry ceiling before the job is dropped.

    Returns:
        Outcome, job kind, and resulting requeue count.
    """
    raw_result = await _eval(
        valkey,
        _REAP_STALE_JOB_SCRIPT,
        6,
        settings.queue_inflight_times_key,
        settings.queue_inflight_key,
        f"{settings.queue_job_hash_prefix}:{job_id}",
        f"judge:lock:{job_id}",
        settings.queue_pending_key,
        settings.queue_profiling_key,
        job_id,
        cutoff_epoch,
        max_requeue_count,
    )
    outcome, job_kind, requeue_count = (_decode_scalar(item) for item in raw_result)
    return cast(ReaperOutcome, outcome), job_kind, int(requeue_count)


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
