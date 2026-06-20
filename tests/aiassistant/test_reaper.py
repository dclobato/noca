#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Integration tests for AI review Valkey helpers and stale-job reaping."""

from __future__ import annotations

import logging
import time
from uuid import uuid4

import pytest
import valkey.asyncio as aivalkey

from aiassistant.reaper import _handle_stale_job
from shared.queue_schema import ArenaAIReviewJob
from shared.services.valkey_service import ValkeyRuntime
from shared.services.valkey_service.constants import (
    QUEUE_AI_REVIEW_INFLIGHT_KEY,
    QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY,
    QUEUE_AI_REVIEW_JOB_HASH_PREFIX,
    QUEUE_AI_REVIEW_PENDING_KEY,
)
from shared.services.valkey_service.queue_ops import (
    dequeue_arena_ai_review_job_id,
    enqueue_arena_ai_review_job,
    get_ai_review_job_hash,
    get_stale_ai_review_job_ids,
)


def _runtime_from_client(client: aivalkey.Valkey) -> ValkeyRuntime:
    """Build a ValkeyRuntime wrapper around the fixture-owned real client."""
    runtime = ValkeyRuntime(valkey_url="redis://127.0.0.1:6379/15", healthcheck_interval_s=60)
    runtime._client = client  # type: ignore[assignment]
    runtime._is_available = True
    return runtime


def _job(submission_id: str | None = None, *, requeue_count: int = 0) -> ArenaAIReviewJob:
    """Return a valid AI review job with generated ids."""
    return ArenaAIReviewJob(
        submission_id=submission_id or str(uuid4()),
        user_id=str(uuid4()),
        problem_id=str(uuid4()),
        language_id="python3",
        requeue_count=requeue_count,
    )


@pytest.mark.asyncio
async def test_dequeue_records_inflight_timestamp_for_real_valkey(
    valkey_client: aivalkey.Valkey,
) -> None:
    """Dequeue moves to inflight and writes the ZSET timestamp used by the reaper."""
    job = _job()
    await enqueue_arena_ai_review_job(valkey_client, job)

    result = await dequeue_arena_ai_review_job_id(valkey_client)

    assert result == job.submission_id
    score = await valkey_client.zscore(QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY, job.submission_id)
    assert score is not None
    assert float(score) <= time.time()


@pytest.mark.asyncio
async def test_stale_job_scan_and_hash_read_use_real_valkey(
    valkey_client: aivalkey.Valkey,
) -> None:
    """Stale scan returns only old inflight ids and hash reads decode metadata."""
    stale = _job()
    fresh = _job()
    await enqueue_arena_ai_review_job(valkey_client, stale)
    await enqueue_arena_ai_review_job(valkey_client, fresh)
    await valkey_client.zadd(
        QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY,
        {
            stale.submission_id: time.time() - 600,
            fresh.submission_id: time.time(),
        },
    )

    stale_ids = await get_stale_ai_review_job_ids(valkey_client, stale_threshold_s=300)
    stale_hash = await get_ai_review_job_hash(valkey_client, stale.submission_id)

    assert stale_ids == [stale.submission_id]
    assert stale_hash is not None
    assert stale_hash["submission_id"] == stale.submission_id
    assert stale_hash["job_kind"] == "arena_ai_review"


@pytest.mark.asyncio
async def test_handle_stale_job_requeues_with_incremented_count(
    valkey_client: aivalkey.Valkey,
) -> None:
    """A stale inflight job below the retry limit is removed and re-enqueued."""
    runtime = _runtime_from_client(valkey_client)
    job = _job(requeue_count=1)
    await enqueue_arena_ai_review_job(valkey_client, job)
    await valkey_client.lrem(QUEUE_AI_REVIEW_PENDING_KEY, 1, job.submission_id)
    await valkey_client.lpush(QUEUE_AI_REVIEW_INFLIGHT_KEY, job.submission_id)
    await valkey_client.zadd(QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY, {job.submission_id: time.time() - 600})

    await _handle_stale_job(
        valkey_runtime=runtime,
        submission_id=job.submission_id,
        max_requeue_count=3,
        logger=logging.getLogger(__name__),
    )

    assert await valkey_client.lrange(QUEUE_AI_REVIEW_INFLIGHT_KEY, 0, -1) == []
    assert await valkey_client.zrange(QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY, 0, -1) == []
    assert await valkey_client.lrange(QUEUE_AI_REVIEW_PENDING_KEY, 0, -1) == [job.submission_id]
    stored = await valkey_client.hgetall(f"{QUEUE_AI_REVIEW_JOB_HASH_PREFIX}:{job.submission_id}")
    assert stored["requeue_count"] == "2"


@pytest.mark.asyncio
async def test_handle_stale_legacy_job_preserves_missing_platform_key_flag(
    valkey_client: aivalkey.Valkey,
) -> None:
    """A legacy stale job keeps omitting use_platform_key after being requeued."""
    runtime = _runtime_from_client(valkey_client)
    job = _job(requeue_count=1)
    await enqueue_arena_ai_review_job(valkey_client, job)
    await valkey_client.hdel(f"{QUEUE_AI_REVIEW_JOB_HASH_PREFIX}:{job.submission_id}", "use_platform_key")
    await valkey_client.lrem(QUEUE_AI_REVIEW_PENDING_KEY, 1, job.submission_id)
    await valkey_client.lpush(QUEUE_AI_REVIEW_INFLIGHT_KEY, job.submission_id)
    await valkey_client.zadd(QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY, {job.submission_id: time.time() - 600})

    await _handle_stale_job(
        valkey_runtime=runtime,
        submission_id=job.submission_id,
        max_requeue_count=3,
        logger=logging.getLogger(__name__),
    )

    stored = await valkey_client.hgetall(f"{QUEUE_AI_REVIEW_JOB_HASH_PREFIX}:{job.submission_id}")
    assert stored["requeue_count"] == "2"
    assert "use_platform_key" not in stored


@pytest.mark.asyncio
async def test_handle_stale_job_over_retry_limit_is_not_requeued(
    valkey_client: aivalkey.Valkey,
) -> None:
    """A stale job beyond the retry limit has all Valkey state removed."""
    runtime = _runtime_from_client(valkey_client)
    job = _job(requeue_count=3)
    await enqueue_arena_ai_review_job(valkey_client, job)
    await valkey_client.lrem(QUEUE_AI_REVIEW_PENDING_KEY, 1, job.submission_id)
    await valkey_client.lpush(QUEUE_AI_REVIEW_INFLIGHT_KEY, job.submission_id)
    await valkey_client.zadd(QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY, {job.submission_id: time.time() - 600})

    await _handle_stale_job(
        valkey_runtime=runtime,
        submission_id=job.submission_id,
        max_requeue_count=3,
        logger=logging.getLogger(__name__),
    )

    assert await valkey_client.lrange(QUEUE_AI_REVIEW_INFLIGHT_KEY, 0, -1) == []
    assert await valkey_client.zrange(QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY, 0, -1) == []
    assert await valkey_client.lrange(QUEUE_AI_REVIEW_PENDING_KEY, 0, -1) == []
    assert await valkey_client.exists(f"{QUEUE_AI_REVIEW_JOB_HASH_PREFIX}:{job.submission_id}") == 0
