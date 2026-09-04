#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Low-level Valkey queue operations and convenience wrappers."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import cast

import valkey.asyncio as aivalkey

from shared.queue_schema import (
    AllContestQueueMetrics,
    ArenaAIReviewJob,
    ArenaSubmissionJob,
    ArenaVerdictEvent,
    ContestQueueMetrics,
    CustomValidatorValidationJob,
    JudgeJob,
    MailJob,
    ProfilingJob,
    SolutionTestJob,
    SubmissionEvent,
    VerdictEvent,
)
from shared.services.valkey_service.constants import (
    ARENA_RESULTS_CHANNEL,
    QUEUE_AI_REVIEW_INFLIGHT_KEY,
    QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY,
    QUEUE_AI_REVIEW_JOB_HASH_PREFIX,
    QUEUE_AI_REVIEW_PENDING_KEY,
    QUEUE_INFLIGHT_KEY,
    QUEUE_INFLIGHT_TIMES_KEY,
    QUEUE_JOB_HASH_PREFIX,
    QUEUE_MAIL_INFLIGHT_KEY,
    QUEUE_MAIL_INFLIGHT_TIMES_KEY,
    QUEUE_MAIL_JOB_HASH_PREFIX,
    QUEUE_MAIL_PENDING_KEY,
    QUEUE_PENDING_KEY,
    QUEUE_PRIORITY_KEY,
    QUEUE_PROFILING_KEY,
    QUEUE_RESULTS_CHANNEL,
    QUEUE_SUBMISSIONS_CHANNEL,
)

logger = logging.getLogger(__name__)


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


async def enqueue_job_with_client(client: aivalkey.Valkey, job: JudgeJob, *, priority: bool) -> None:
    """Store a JudgeJob hash and push its judgment_id onto the proper queue list."""
    queue_key = QUEUE_PRIORITY_KEY if priority else QUEUE_PENDING_KEY
    job_key = f"{QUEUE_JOB_HASH_PREFIX}:{job.judgment_id}"
    job_mapping = {
        key: str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in job.model_dump(mode="python", exclude_none=True).items()
    }
    pipe = client.pipeline()
    pipe.hset(job_key, mapping=job_mapping)
    pipe.lpush(queue_key, job.judgment_id)
    await pipe.execute()


async def enqueue_profiling_job_with_client(client: aivalkey.Valkey, job: ProfilingJob) -> None:
    """Store a ProfilingJob hash and push its profiling_run_id to the profiling queue."""
    job_key = f"{QUEUE_JOB_HASH_PREFIX}:{job.profiling_run_id}"
    job_mapping = {
        key: str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in job.model_dump(mode="python", exclude_none=True).items()
    }
    pipe = client.pipeline()
    pipe.hset(job_key, mapping=job_mapping)
    pipe.lpush(QUEUE_PROFILING_KEY, job.profiling_run_id)
    await pipe.execute()


async def enqueue_solution_test_job_with_client(
    client: aivalkey.Valkey, job: SolutionTestJob, *, priority: bool
) -> None:
    """Store a SolutionTestJob hash and push its run id onto the proper queue list.

    Solution tests share the contestant queues and the ``priority=contest.is_running``
    rule, so staff testing competes for judge capacity exactly like a real submission.
    """
    queue_key = QUEUE_PRIORITY_KEY if priority else QUEUE_PENDING_KEY
    job_key = f"{QUEUE_JOB_HASH_PREFIX}:{job.solution_test_run_id}"
    job_mapping = {
        key: str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in job.model_dump(mode="python", exclude_none=True).items()
    }
    pipe = client.pipeline()
    pipe.hset(job_key, mapping=job_mapping)
    pipe.lpush(queue_key, job.solution_test_run_id)
    await pipe.execute()


async def enqueue_custom_validator_validation_job_with_client(
    client: aivalkey.Valkey, job: CustomValidatorValidationJob
) -> None:
    """Store a validator-validation job on the profiling-priority queue."""
    job_key = f"{QUEUE_JOB_HASH_PREFIX}:{job.validation_id}"
    pipe = client.pipeline()
    pipe.hset(job_key, mapping={key: str(value) for key, value in job.model_dump().items()})
    pipe.lpush(QUEUE_PROFILING_KEY, job.validation_id)
    await pipe.execute()


async def enqueue_arena_submission_job_with_client(client: aivalkey.Valkey, job: ArenaSubmissionJob) -> None:
    """Store an ArenaSubmissionJob hash and push its judgment_id onto the pending queue."""
    job_key = f"{QUEUE_JOB_HASH_PREFIX}:{job.judgment_id}"
    job_mapping = {
        key: str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in job.model_dump(mode="python", exclude_none=True).items()
    }
    pipe = client.pipeline()
    pipe.hset(job_key, mapping=job_mapping)
    pipe.lpush(QUEUE_PENDING_KEY, job.judgment_id)
    await pipe.execute()


_DEQUEUE_AI_REVIEW_JOB_SCRIPT = """
local job_id = redis.call("RPOP", KEYS[1])
if job_id then
    redis.call("LPUSH", KEYS[2], job_id)
    return job_id
end
return nil
"""


async def enqueue_arena_ai_review_job_with_client(client: aivalkey.Valkey, job: ArenaAIReviewJob) -> None:
    """Store an ArenaAIReviewJob hash and push its submission_id onto the AI review pending queue."""
    job_key = f"{QUEUE_AI_REVIEW_JOB_HASH_PREFIX}:{job.submission_id}"
    job_mapping = {
        key: str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in job.model_dump(mode="python", exclude_none=True).items()
    }
    pipe = client.pipeline()
    pipe.hset(job_key, mapping=job_mapping)
    pipe.lpush(QUEUE_AI_REVIEW_PENDING_KEY, job.submission_id)
    await pipe.execute()


async def dequeue_arena_ai_review_job_id_with_client(client: aivalkey.Valkey) -> str | None:
    """Move one submission_id from pending to inflight and record dispatch timestamp.

    The Lua script atomically moves the item from ``ai:queue:pending`` to
    ``ai:queue:inflight``. After a successful dequeue the dispatch time is written
    to ``ai:queue:inflight:times`` (ZSET, score = epoch seconds) so the reaper can
    detect stale jobs.
    """
    import time

    result = client.eval(
        _DEQUEUE_AI_REVIEW_JOB_SCRIPT,
        2,
        QUEUE_AI_REVIEW_PENDING_KEY,
        QUEUE_AI_REVIEW_INFLIGHT_KEY,
    )
    if asyncio.iscoroutine(result):
        result = await result
    if result is not None:
        submission_id = result.decode() if isinstance(result, bytes) else cast(str, result)
        await client.zadd(QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY, {submission_id: time.time()})
        return submission_id
    return None


async def remove_from_ai_review_inflight_with_client(client: aivalkey.Valkey, submission_id: str) -> None:
    """Remove a submission_id from the AI review inflight list and dispatch-time sorted set."""
    pipe = client.pipeline()
    pipe.lrem(QUEUE_AI_REVIEW_INFLIGHT_KEY, 1, submission_id)
    pipe.zrem(QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY, submission_id)
    await pipe.execute()


async def complete_arena_ai_review_job_with_client(client: aivalkey.Valkey, submission_id: str) -> None:
    """Atomically remove all Valkey state for a terminal AI review job."""
    job_key = f"{QUEUE_AI_REVIEW_JOB_HASH_PREFIX}:{submission_id}"
    pipe = client.pipeline(transaction=True)
    pipe.lrem(QUEUE_AI_REVIEW_PENDING_KEY, 0, submission_id)
    pipe.lrem(QUEUE_AI_REVIEW_INFLIGHT_KEY, 0, submission_id)
    pipe.zrem(QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY, submission_id)
    pipe.delete(job_key)
    await pipe.execute()


async def get_stale_ai_review_job_ids_with_client(client: aivalkey.Valkey, stale_threshold_s: float) -> list[str]:
    """Return submission_ids that have been inflight longer than stale_threshold_s seconds.

    Queries ``ai:queue:inflight:times`` (ZSET) for members with score in
    ``[0, now - stale_threshold_s]``.

    Args:
        client: Live Valkey async client.
        stale_threshold_s: Age in seconds after which a job is considered stale.

    Returns:
        List of submission_id strings (may be empty).
    """
    import time

    cutoff = time.time() - stale_threshold_s
    raw_ids = await client.zrangebyscore(QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY, min=0, max=cutoff)
    return [r.decode() if isinstance(r, bytes) else cast(str, r) for r in raw_ids]


async def get_ai_review_queued_ids_with_client(client: aivalkey.Valkey) -> set[str]:
    """Return submission_ids currently in the AI review pending or inflight queues.

    Reads both ``ai:queue:pending`` and ``ai:queue:inflight`` in a single
    pipeline and returns the union as a set. Used by the reconciler to tell
    whether a submission flagged ``submit_to_ai`` still has live queue presence.

    Args:
        client: Live Valkey async client.

    Returns:
        Set of submission_id strings (may be empty).
    """
    pipe = client.pipeline()
    pipe.lrange(QUEUE_AI_REVIEW_PENDING_KEY, 0, -1)
    pipe.lrange(QUEUE_AI_REVIEW_INFLIGHT_KEY, 0, -1)
    pending, inflight = await pipe.execute()
    return {(r.decode() if isinstance(r, bytes) else cast(str, r)) for r in (*pending, *inflight)}


async def get_ai_review_job_hash_with_client(client: aivalkey.Valkey, submission_id: str) -> dict[str, str] | None:
    """Return the job metadata hash stored at ``ai:job:<submission_id>``.

    Args:
        client: Live Valkey async client.
        submission_id: Submission UUID whose hash to retrieve.

    Returns:
        Dict of string fields, or None when the hash does not exist.
    """
    raw = client.hgetall(f"{QUEUE_AI_REVIEW_JOB_HASH_PREFIX}:{submission_id}")
    data: dict[object, object] = await raw if asyncio.iscoroutine(raw) else await raw  # type: ignore[misc]
    if not data:
        return None
    return {
        (k.decode() if isinstance(k, bytes) else str(k)): (v.decode() if isinstance(v, bytes) else str(v))
        for k, v in data.items()
    }


async def dequeue_job_id_with_client(client: aivalkey.Valkey) -> str | None:
    """Move one ready queued id into inflight, preferring profiling and priority work first."""
    ready_result = client.eval(
        _DEQUEUE_READY_JOB_SCRIPT,
        4,
        QUEUE_PROFILING_KEY,
        QUEUE_PRIORITY_KEY,
        QUEUE_PENDING_KEY,
        QUEUE_INFLIGHT_KEY,
    )
    if asyncio.iscoroutine(ready_result):
        ready_result = await ready_result
    if ready_result is not None:
        return ready_result.decode() if isinstance(ready_result, bytes) else cast(str, ready_result)
    return None


async def remove_from_inflight_with_client(client: aivalkey.Valkey, judgment_id: str) -> None:
    """Remove a judgment_id from the inflight list and dispatch-time sorted set."""
    pipe = client.pipeline()
    pipe.lrem(QUEUE_INFLIGHT_KEY, 1, judgment_id)
    pipe.zrem(QUEUE_INFLIGHT_TIMES_KEY, judgment_id)
    await pipe.execute()


async def publish_verdict_with_client(client: aivalkey.Valkey, event: VerdictEvent) -> None:
    """Publish a VerdictEvent to the Valkey pub/sub results channel."""
    await client.publish(
        QUEUE_RESULTS_CHANNEL,
        event.model_dump_json(),
    )


async def publish_submission_with_client(client: aivalkey.Valkey, event: SubmissionEvent) -> None:
    """Publish a SubmissionEvent to the Valkey pub/sub submissions channel."""
    await client.publish(
        QUEUE_SUBMISSIONS_CHANNEL,
        event.model_dump_json(),
    )


async def publish_arena_verdict_with_client(client: aivalkey.Valkey, event: ArenaVerdictEvent) -> None:
    """Publish an ArenaVerdictEvent to the Valkey Arena pub/sub results channel."""
    await client.publish(
        ARENA_RESULTS_CHANNEL,
        event.model_dump_json(),
    )


async def enqueue_job(client_or_runtime: aivalkey.Valkey | object, job: JudgeJob, *, priority: bool) -> None:
    """Enqueue a JudgeJob for processing."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        await client_or_runtime.enqueue_job(job, priority=priority)
        return
    await enqueue_job_with_client(cast(aivalkey.Valkey, client_or_runtime), job, priority=priority)


async def enqueue_profiling_job(client_or_runtime: aivalkey.Valkey | object, job: ProfilingJob) -> None:
    """Enqueue a ProfilingJob for processing."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        await client_or_runtime.enqueue_profiling_job(job)
        return
    await enqueue_profiling_job_with_client(cast(aivalkey.Valkey, client_or_runtime), job)


async def enqueue_solution_test_job(
    client_or_runtime: aivalkey.Valkey | object, job: SolutionTestJob, *, priority: bool
) -> None:
    """Enqueue a non-scoring solution-test run for processing."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        await client_or_runtime.enqueue_solution_test_job(job, priority=priority)
        return
    await enqueue_solution_test_job_with_client(cast(aivalkey.Valkey, client_or_runtime), job, priority=priority)


async def enqueue_custom_validator_validation_job(
    client_or_runtime: aivalkey.Valkey | object, job: CustomValidatorValidationJob
) -> None:
    """Enqueue validator compilation after its database transaction commits."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        await client_or_runtime.enqueue_custom_validator_validation_job(job)
        return
    await enqueue_custom_validator_validation_job_with_client(cast(aivalkey.Valkey, client_or_runtime), job)


async def enqueue_arena_submission_job(client_or_runtime: aivalkey.Valkey | object, job: ArenaSubmissionJob) -> None:
    """Enqueue an Arena submission judgment for processing."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        await client_or_runtime.enqueue_arena_submission_job(job)
        return
    await enqueue_arena_submission_job_with_client(cast(aivalkey.Valkey, client_or_runtime), job)


async def dequeue_job_id(client_or_runtime: aivalkey.Valkey | object) -> str | None:
    """Dequeue a job id from the queue."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        return await client_or_runtime.dequeue_job_id()
    return await dequeue_job_id_with_client(cast(aivalkey.Valkey, client_or_runtime))


async def remove_from_inflight(client_or_runtime: aivalkey.Valkey | object, judgment_id: str) -> None:
    """Remove a judgment_id from the inflight list."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        await client_or_runtime.remove_from_inflight(judgment_id)
        return
    try:
        await remove_from_inflight_with_client(cast(aivalkey.Valkey, client_or_runtime), judgment_id)
    except Exception as exc:
        logger.error(f"Failed to remove job '{judgment_id}' from inflight list: {str(exc)}")


async def publish_verdict(client_or_runtime: aivalkey.Valkey | object, event: VerdictEvent) -> None:
    """Publish a VerdictEvent to the pub/sub results channel."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        await client_or_runtime.publish_verdict(event)
        return
    try:
        await publish_verdict_with_client(cast(aivalkey.Valkey, client_or_runtime), event)
    except Exception as exc:
        logger.error("Failed to publish verdict event")
        logger.error(
            json.dumps({"submission_id": event.submission_id, "verdict": event.verdict, "error": str(exc)}, indent=2)
        )


async def publish_submission(client_or_runtime: aivalkey.Valkey | object, event: SubmissionEvent) -> None:
    """Publish a SubmissionEvent to the pub/sub submissions channel."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        await client_or_runtime.publish_submission(event)
        return
    try:
        await publish_submission_with_client(cast(aivalkey.Valkey, client_or_runtime), event)
    except Exception as exc:
        logger.error("Failed to publish submission event")
        logger.error(
            json.dumps(
                {"submission_id": event.submission_id, "contest_id": event.contest_id, "error": str(exc)},
                indent=2,
            )
        )


async def enqueue_arena_ai_review_job(client_or_runtime: aivalkey.Valkey | object, job: ArenaAIReviewJob) -> None:
    """Enqueue an Arena AI review request for processing."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        await client_or_runtime.enqueue_arena_ai_review_job(job)
        return
    await enqueue_arena_ai_review_job_with_client(cast(aivalkey.Valkey, client_or_runtime), job)


async def dequeue_arena_ai_review_job_id(client_or_runtime: aivalkey.Valkey | object) -> str | None:
    """Dequeue the next AI review submission_id from the pending queue."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        return await client_or_runtime.dequeue_arena_ai_review_job_id()
    return await dequeue_arena_ai_review_job_id_with_client(cast(aivalkey.Valkey, client_or_runtime))


async def remove_from_ai_review_inflight(client_or_runtime: aivalkey.Valkey | object, submission_id: str) -> None:
    """Remove a submission_id from the AI review inflight list."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        await client_or_runtime.remove_from_ai_review_inflight(submission_id)
        return
    try:
        await remove_from_ai_review_inflight_with_client(cast(aivalkey.Valkey, client_or_runtime), submission_id)
    except Exception as exc:
        logger.error(f"Failed to remove AI review job '{submission_id}' from inflight list: {str(exc)}")


async def complete_arena_ai_review_job(client_or_runtime: aivalkey.Valkey | object, submission_id: str) -> None:
    """Remove all Valkey state for a terminal Arena AI review job."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        await client_or_runtime.complete_arena_ai_review_job(submission_id)
        return
    try:
        await complete_arena_ai_review_job_with_client(cast(aivalkey.Valkey, client_or_runtime), submission_id)
    except Exception as exc:
        logger.error(f"Failed to complete AI review job '{submission_id}': {str(exc)}")


async def get_stale_ai_review_job_ids(
    client_or_runtime: aivalkey.Valkey | object, stale_threshold_s: float
) -> list[str]:
    """Return submission_ids inflight longer than stale_threshold_s seconds."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        return await client_or_runtime.get_stale_ai_review_job_ids(stale_threshold_s)
    return await get_stale_ai_review_job_ids_with_client(cast(aivalkey.Valkey, client_or_runtime), stale_threshold_s)


async def get_ai_review_job_hash(
    client_or_runtime: aivalkey.Valkey | object, submission_id: str
) -> dict[str, str] | None:
    """Return the job metadata hash for the given submission_id, or None."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        return await client_or_runtime.get_ai_review_job_hash(submission_id)
    return await get_ai_review_job_hash_with_client(cast(aivalkey.Valkey, client_or_runtime), submission_id)


async def get_ai_review_queued_ids(client_or_runtime: aivalkey.Valkey | object) -> set[str]:
    """Return submission_ids currently queued (pending or inflight) for AI review."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        return await client_or_runtime.get_ai_review_queued_ids()
    return await get_ai_review_queued_ids_with_client(cast(aivalkey.Valkey, client_or_runtime))


async def get_contest_queue_metrics(
    client_or_runtime: aivalkey.Valkey | object,
    contest_id: str,
) -> ContestQueueMetrics | None:
    """Return queue metrics for one contest."""
    from shared.services.valkey_service import _get_contest_queue_metrics_with_client
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        return await client_or_runtime.get_contest_queue_metrics(contest_id)
    return await _get_contest_queue_metrics_with_client(cast(aivalkey.Valkey, client_or_runtime), contest_id)


async def get_all_contest_queue_metrics(
    client_or_runtime: aivalkey.Valkey | object,
) -> AllContestQueueMetrics | None:
    """Return per-contest queue aggregation across all contests."""
    from shared.services.valkey_service import _get_all_contest_queue_metrics_with_client
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        return await client_or_runtime.get_all_contest_queue_metrics()
    return await _get_all_contest_queue_metrics_with_client(cast(aivalkey.Valkey, client_or_runtime))


# ---------------------------------------------------------------------------
# Outbound email queue (drained by the mailer worker)
# ---------------------------------------------------------------------------

_DEQUEUE_MAIL_JOB_SCRIPT = """
local job_id = redis.call("RPOP", KEYS[1])
if job_id then
    redis.call("LPUSH", KEYS[2], job_id)
    redis.call("ZADD", KEYS[3], ARGV[1], job_id)
    return job_id
end
return nil
"""

# Requeue-or-drop for one stale inflight job, in one atomic step so a crash
# between "remove from inflight" and "push to pending" can never lose the job,
# and a completion racing with the reaper can never resurrect a delivered
# message: the job is only touched while it is still inflight and its hash
# still exists. The hash keeps its original TTL -- a retry must not extend
# the credential-at-rest bound.
_REQUEUE_STALE_MAIL_JOB_SCRIPT = """
local jid = ARGV[1]
local max_requeues = tonumber(ARGV[2])
if not redis.call("LPOS", KEYS[2], jid) then
    redis.call("ZREM", KEYS[3], jid)
    return "not_inflight"
end
if redis.call("EXISTS", KEYS[4]) == 0 then
    redis.call("LREM", KEYS[2], 0, jid)
    redis.call("ZREM", KEYS[3], jid)
    return "expired"
end
local requeue_count = tonumber(redis.call("HGET", KEYS[4], "requeue_count") or "0") or 0
if requeue_count >= max_requeues then
    redis.call("DEL", KEYS[4])
    redis.call("LREM", KEYS[2], 0, jid)
    redis.call("ZREM", KEYS[3], jid)
    return "dropped"
end
redis.call("HSET", KEYS[4], "requeue_count", requeue_count + 1)
redis.call("LREM", KEYS[2], 0, jid)
redis.call("ZREM", KEYS[3], jid)
redis.call("LPUSH", KEYS[1], jid)
return "requeued"
"""


async def enqueue_mail_job_with_client(client: aivalkey.Valkey, job: MailJob, *, ttl_seconds: int) -> None:
    """Store a MailJob hash (with a TTL) and push its job_id onto the mail pending queue.

    The TTL is the credential-at-rest bound: a rendered password that nobody
    delivered within ``ttl_seconds`` disappears from Valkey on its own, and the
    worker drops a list entry whose hash is gone.
    """
    job_key = f"{QUEUE_MAIL_JOB_HASH_PREFIX}:{job.job_id}"
    job_mapping = {
        key: str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in job.model_dump(mode="python", exclude_none=True).items()
    }
    pipe = client.pipeline()
    pipe.hset(job_key, mapping=job_mapping)
    pipe.expire(job_key, ttl_seconds)
    pipe.lpush(QUEUE_MAIL_PENDING_KEY, job.job_id)
    await pipe.execute()


async def dequeue_mail_job_id_with_client(client: aivalkey.Valkey) -> str | None:
    """Move one job_id from pending to inflight and record its dispatch timestamp.

    The move and the timestamp are one Lua script: a crash between them would
    otherwise leave a job inflight that the reaper can never see.
    """
    import time

    result = client.eval(
        _DEQUEUE_MAIL_JOB_SCRIPT,
        3,
        QUEUE_MAIL_PENDING_KEY,
        QUEUE_MAIL_INFLIGHT_KEY,
        QUEUE_MAIL_INFLIGHT_TIMES_KEY,
        str(time.time()),
    )
    if asyncio.iscoroutine(result):
        result = await result
    if result is not None:
        return result.decode() if isinstance(result, bytes) else cast(str, result)
    return None


async def requeue_stale_mail_job_with_client(client: aivalkey.Valkey, job_id: str, *, max_requeue_count: int) -> str:
    """Atomically requeue one stale inflight job, or drop it past the requeue cap.

    Returns one of ``"requeued"``, ``"dropped"`` (cap reached; the hash is
    deleted), ``"expired"`` (the hash TTL already removed the payload; only
    the inflight entry is cleaned) or ``"not_inflight"`` (completed by the
    worker in the meantime; nothing to do). The hash keeps its TTL.
    """
    result = client.eval(
        _REQUEUE_STALE_MAIL_JOB_SCRIPT,
        4,
        QUEUE_MAIL_PENDING_KEY,
        QUEUE_MAIL_INFLIGHT_KEY,
        QUEUE_MAIL_INFLIGHT_TIMES_KEY,
        f"{QUEUE_MAIL_JOB_HASH_PREFIX}:{job_id}",
        job_id,
        str(max_requeue_count),
    )
    if asyncio.iscoroutine(result):
        result = await result
    return result.decode() if isinstance(result, bytes) else cast(str, result)


async def remove_from_mail_inflight_with_client(client: aivalkey.Valkey, job_id: str) -> None:
    """Remove a job_id from the mail inflight list and dispatch-time sorted set."""
    pipe = client.pipeline()
    pipe.lrem(QUEUE_MAIL_INFLIGHT_KEY, 1, job_id)
    pipe.zrem(QUEUE_MAIL_INFLIGHT_TIMES_KEY, job_id)
    await pipe.execute()


async def complete_mail_job_with_client(client: aivalkey.Valkey, job_id: str) -> None:
    """Atomically remove all Valkey state for a terminal mail job."""
    job_key = f"{QUEUE_MAIL_JOB_HASH_PREFIX}:{job_id}"
    pipe = client.pipeline(transaction=True)
    pipe.lrem(QUEUE_MAIL_PENDING_KEY, 0, job_id)
    pipe.lrem(QUEUE_MAIL_INFLIGHT_KEY, 0, job_id)
    pipe.zrem(QUEUE_MAIL_INFLIGHT_TIMES_KEY, job_id)
    pipe.delete(job_key)
    await pipe.execute()


async def get_stale_mail_job_ids_with_client(client: aivalkey.Valkey, stale_threshold_s: float) -> list[str]:
    """Return job_ids inflight longer than ``stale_threshold_s`` seconds."""
    import time

    cutoff = time.time() - stale_threshold_s
    raw_ids = await client.zrangebyscore(QUEUE_MAIL_INFLIGHT_TIMES_KEY, min=0, max=cutoff)
    return [r.decode() if isinstance(r, bytes) else cast(str, r) for r in raw_ids]


async def get_mail_job_hash_with_client(client: aivalkey.Valkey, job_id: str) -> dict[str, str] | None:
    """Return the job hash stored at ``mail:job:<job_id>``, or None when expired/absent."""
    raw = client.hgetall(f"{QUEUE_MAIL_JOB_HASH_PREFIX}:{job_id}")
    data: dict[object, object] = await raw if asyncio.iscoroutine(raw) else await raw  # type: ignore[misc]
    if not data:
        return None
    return {
        (k.decode() if isinstance(k, bytes) else str(k)): (v.decode() if isinstance(v, bytes) else str(v))
        for k, v in data.items()
    }


async def enqueue_mail_job(client_or_runtime: aivalkey.Valkey | object, job: MailJob, *, ttl_seconds: int) -> None:
    """Enqueue one rendered email for the mailer worker."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        await client_or_runtime.enqueue_mail_job(job, ttl_seconds=ttl_seconds)
        return
    await enqueue_mail_job_with_client(cast(aivalkey.Valkey, client_or_runtime), job, ttl_seconds=ttl_seconds)


async def dequeue_mail_job_id(client_or_runtime: aivalkey.Valkey | object) -> str | None:
    """Dequeue the next mail job_id from the pending queue."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        return await client_or_runtime.dequeue_mail_job_id()
    return await dequeue_mail_job_id_with_client(cast(aivalkey.Valkey, client_or_runtime))


async def requeue_stale_mail_job(
    client_or_runtime: aivalkey.Valkey | object, job_id: str, *, max_requeue_count: int
) -> str:
    """Atomically requeue or drop one stale mail job; see the ``_with_client`` variant."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        return await client_or_runtime.requeue_stale_mail_job(job_id, max_requeue_count=max_requeue_count)
    return await requeue_stale_mail_job_with_client(
        cast(aivalkey.Valkey, client_or_runtime), job_id, max_requeue_count=max_requeue_count
    )


async def remove_from_mail_inflight(client_or_runtime: aivalkey.Valkey | object, job_id: str) -> None:
    """Remove a job_id from the mail inflight list."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        await client_or_runtime.remove_from_mail_inflight(job_id)
        return
    try:
        await remove_from_mail_inflight_with_client(cast(aivalkey.Valkey, client_or_runtime), job_id)
    except Exception as exc:
        logger.error(f"Failed to remove mail job '{job_id}' from inflight list: {str(exc)}")


async def complete_mail_job(client_or_runtime: aivalkey.Valkey | object, job_id: str) -> None:
    """Remove all Valkey state for a terminal mail job."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        await client_or_runtime.complete_mail_job(job_id)
        return
    try:
        await complete_mail_job_with_client(cast(aivalkey.Valkey, client_or_runtime), job_id)
    except Exception as exc:
        logger.error(f"Failed to complete mail job '{job_id}': {str(exc)}")


async def get_stale_mail_job_ids(client_or_runtime: aivalkey.Valkey | object, stale_threshold_s: float) -> list[str]:
    """Return mail job_ids inflight longer than stale_threshold_s seconds."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        return await client_or_runtime.get_stale_mail_job_ids(stale_threshold_s)
    return await get_stale_mail_job_ids_with_client(cast(aivalkey.Valkey, client_or_runtime), stale_threshold_s)


async def get_mail_job_hash(client_or_runtime: aivalkey.Valkey | object, job_id: str) -> dict[str, str] | None:
    """Return the mail job hash for the given job_id, or None."""
    from shared.services.valkey_service.runtime import ValkeyRuntime

    if isinstance(client_or_runtime, ValkeyRuntime):
        return await client_or_runtime.get_mail_job_hash(job_id)
    return await get_mail_job_hash_with_client(cast(aivalkey.Valkey, client_or_runtime), job_id)
