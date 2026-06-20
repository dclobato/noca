#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the ArenaAIReviewJob queue payload and its Valkey operations.

Covers:
  - ArenaAIReviewJob Pydantic validation (required fields, defaults, immutability).
  - JobKind.ARENA_AI_REVIEW discriminator value.
  - enqueue_arena_ai_review_job: hash stored + submission_id pushed to pending queue.
  - dequeue_arena_ai_review_job_id: moves item from pending to inflight atomically.
  - dequeue_arena_ai_review_job_id: returns None when the queue is empty.
  - remove_from_ai_review_inflight: removes from both inflight list and ZSET.
  - complete_arena_ai_review_job: removes every queue entry and the job hash.
  - ValkeyRuntime.enqueue_arena_ai_review_job: buffers command on write failure.
  - ValkeyRuntime.enqueue_arena_ai_review_job: flushes buffered command on recovery.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import valkey.asyncio as aivalkey
from pydantic import ValidationError
from valkey.exceptions import ConnectionError as ValkeyConnectionError

from shared.queue_schema import ArenaAIReviewJob, JobKind
from shared.services.valkey_service import ValkeyRuntime
from shared.services.valkey_service.constants import (
    QUEUE_AI_REVIEW_INFLIGHT_KEY,
    QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY,
    QUEUE_AI_REVIEW_JOB_HASH_PREFIX,
    QUEUE_AI_REVIEW_PENDING_KEY,
)
from shared.services.valkey_service.queue_ops import (
    complete_arena_ai_review_job,
    dequeue_arena_ai_review_job_id,
    enqueue_arena_ai_review_job,
    remove_from_ai_review_inflight,
)

# ---------------------------------------------------------------------------
# Fake Valkey helpers (unit tests — no real connection needed)
# ---------------------------------------------------------------------------


class _FakePipeline:
    def __init__(self, client: _FakeValkey) -> None:
        self.client = client
        self.commands: list[tuple[str, object, object | None]] = []

    def hset(self, key: str, *, mapping: dict[str, str]) -> None:
        self.commands.append(("hset", key, mapping))

    def lpush(self, key: str, value: str) -> None:
        self.commands.append(("lpush", key, value))

    def lrem(self, key: str, count: int, value: str) -> None:
        self.commands.append(("lrem", key, (count, value)))

    def zrem(self, key: str, value: str) -> None:
        self.commands.append(("zrem", key, value))

    def delete(self, key: str) -> None:
        self.commands.append(("delete", key, None))

    async def execute(self) -> None:
        if self.client.fail_writes:
            raise ValkeyConnectionError("valkey unavailable")
        self.client.executed.extend(self.commands)


class _FakeValkey:
    def __init__(self, *, fail_writes: bool = False) -> None:
        self.fail_writes = fail_writes
        self.executed: list[tuple[str, object, object | None]] = []

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        del transaction
        return _FakePipeline(self)

    async def ping(self) -> bool:
        if self.fail_writes:
            raise ValkeyConnectionError("valkey unavailable")
        return True

    async def aclose(self) -> None:
        return None


# ---------------------------------------------------------------------------
# ArenaAIReviewJob — Pydantic validation
# ---------------------------------------------------------------------------


def test_arena_ai_review_job_requires_submission_id() -> None:
    """submission_id is required; omitting it raises ValidationError."""
    with pytest.raises(ValidationError):
        ArenaAIReviewJob(
            user_id=str(uuid4()),
            problem_id=str(uuid4()),
            language_id="gcc-c17",
        )


def test_arena_ai_review_job_requires_user_id() -> None:
    """user_id is required; omitting it raises ValidationError."""
    with pytest.raises(ValidationError):
        ArenaAIReviewJob(
            submission_id=str(uuid4()),
            problem_id=str(uuid4()),
            language_id="gcc-c17",
        )


def test_arena_ai_review_job_requires_problem_id() -> None:
    """problem_id is required; omitting it raises ValidationError."""
    with pytest.raises(ValidationError):
        ArenaAIReviewJob(
            submission_id=str(uuid4()),
            user_id=str(uuid4()),
            language_id="gcc-c17",
        )


def test_arena_ai_review_job_requires_language_id() -> None:
    """language_id is required; omitting it raises ValidationError."""
    with pytest.raises(ValidationError):
        ArenaAIReviewJob(
            submission_id=str(uuid4()),
            user_id=str(uuid4()),
            problem_id=str(uuid4()),
        )


def test_arena_ai_review_job_defaults() -> None:
    """requeue_count defaults to 0 and job_kind is ARENA_AI_REVIEW."""
    job = ArenaAIReviewJob(
        submission_id=str(uuid4()),
        user_id=str(uuid4()),
        problem_id=str(uuid4()),
        language_id="gcc-c17",
    )
    assert job.requeue_count == 0
    assert job.job_kind == JobKind.ARENA_AI_REVIEW


def test_arena_ai_review_job_is_immutable() -> None:
    """ArenaAIReviewJob is frozen; mutation raises an error."""
    job = ArenaAIReviewJob(
        submission_id=str(uuid4()),
        user_id=str(uuid4()),
        problem_id=str(uuid4()),
        language_id="gcc-c17",
    )
    with pytest.raises(ValidationError):
        job.requeue_count = 5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# JobKind discriminator value
# ---------------------------------------------------------------------------


def test_job_kind_arena_ai_review_value() -> None:
    """JobKind.ARENA_AI_REVIEW must be the string 'arena_ai_review'."""
    assert JobKind.ARENA_AI_REVIEW == "arena_ai_review"


# ---------------------------------------------------------------------------
# Valkey integration — enqueue
# ---------------------------------------------------------------------------


async def test_enqueue_arena_ai_review_job_stores_hash_and_pushes_to_pending(
    valkey_client: aivalkey.Valkey,
) -> None:
    """Hash is written at ai:job:<submission_id> and id is pushed to pending queue."""
    job = ArenaAIReviewJob(
        submission_id=str(uuid4()),
        user_id=str(uuid4()),
        problem_id=str(uuid4()),
        language_id="python3",
        requeue_count=2,
    )

    await enqueue_arena_ai_review_job(valkey_client, job)

    stored = await valkey_client.hgetall(f"{QUEUE_AI_REVIEW_JOB_HASH_PREFIX}:{job.submission_id}")
    assert stored == {
        "submission_id": job.submission_id,
        "user_id": job.user_id,
        "problem_id": job.problem_id,
        "language_id": "python3",
        "use_platform_key": "false",
        "requeue_count": "2",
        "job_kind": "arena_ai_review",
    }
    pending = await valkey_client.lrange(QUEUE_AI_REVIEW_PENDING_KEY, 0, -1)
    assert job.submission_id in pending


# ---------------------------------------------------------------------------
# Valkey integration — dequeue
# ---------------------------------------------------------------------------


async def test_dequeue_arena_ai_review_job_id_moves_item_to_inflight(
    valkey_client: aivalkey.Valkey,
) -> None:
    """Dequeue pops from pending and pushes the id into the inflight list."""
    submission_id = str(uuid4())
    await valkey_client.lpush(QUEUE_AI_REVIEW_PENDING_KEY, submission_id)

    result = await dequeue_arena_ai_review_job_id(valkey_client)

    assert result == submission_id
    assert await valkey_client.lrange(QUEUE_AI_REVIEW_PENDING_KEY, 0, -1) == []
    assert submission_id in await valkey_client.lrange(QUEUE_AI_REVIEW_INFLIGHT_KEY, 0, -1)


async def test_dequeue_arena_ai_review_job_id_returns_none_when_empty(
    valkey_client: aivalkey.Valkey,
) -> None:
    """Dequeue returns None when the pending queue is empty."""
    result = await dequeue_arena_ai_review_job_id(valkey_client)
    assert result is None


async def test_dequeue_arena_ai_review_job_id_fifo_order(
    valkey_client: aivalkey.Valkey,
) -> None:
    """Multiple items are dequeued in FIFO order (LPUSH / RPOP).

    LPUSH pushes to the head of the list; RPOP pops from the tail.
    So the first item pushed ends up at the tail and is dequeued first.
    Enqueue order: first_id → second_id (first_id ends at tail).
    Dequeue order: first_id, then second_id.
    """
    first_id = str(uuid4())
    second_id = str(uuid4())
    await valkey_client.lpush(QUEUE_AI_REVIEW_PENDING_KEY, first_id)
    await valkey_client.lpush(QUEUE_AI_REVIEW_PENDING_KEY, second_id)

    result_a = await dequeue_arena_ai_review_job_id(valkey_client)
    result_b = await dequeue_arena_ai_review_job_id(valkey_client)

    assert result_a == first_id
    assert result_b == second_id


# ---------------------------------------------------------------------------
# Valkey integration — remove from inflight
# ---------------------------------------------------------------------------


async def test_remove_from_ai_review_inflight_cleans_list_and_zset(
    valkey_client: aivalkey.Valkey,
) -> None:
    """remove_from_ai_review_inflight removes from both the inflight list and the ZSET."""
    submission_id = str(uuid4())
    await valkey_client.lpush(QUEUE_AI_REVIEW_INFLIGHT_KEY, submission_id)
    await valkey_client.zadd(QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY, {submission_id: 123.0})

    await remove_from_ai_review_inflight(valkey_client, submission_id)

    assert await valkey_client.lrange(QUEUE_AI_REVIEW_INFLIGHT_KEY, 0, -1) == []
    assert await valkey_client.zrange(QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY, 0, -1) == []


async def test_remove_from_ai_review_inflight_is_idempotent(
    valkey_client: aivalkey.Valkey,
) -> None:
    """Calling remove twice on the same id does not raise."""
    submission_id = str(uuid4())
    await valkey_client.lpush(QUEUE_AI_REVIEW_INFLIGHT_KEY, submission_id)
    await valkey_client.zadd(QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY, {submission_id: 123.0})

    await remove_from_ai_review_inflight(valkey_client, submission_id)
    await remove_from_ai_review_inflight(valkey_client, submission_id)  # second call must not raise


# ---------------------------------------------------------------------------
# Valkey integration — complete terminal job
# ---------------------------------------------------------------------------


async def test_complete_arena_ai_review_job_removes_all_state(
    valkey_client: aivalkey.Valkey,
) -> None:
    """Terminal cleanup removes duplicates, dispatch time, and the job hash."""
    job = ArenaAIReviewJob(
        submission_id=str(uuid4()),
        user_id=str(uuid4()),
        problem_id=str(uuid4()),
        language_id="python3",
    )
    await enqueue_arena_ai_review_job(valkey_client, job)
    await valkey_client.lpush(QUEUE_AI_REVIEW_PENDING_KEY, job.submission_id)
    await valkey_client.lpush(QUEUE_AI_REVIEW_INFLIGHT_KEY, job.submission_id, job.submission_id)
    await valkey_client.zadd(QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY, {job.submission_id: 123.0})

    await complete_arena_ai_review_job(valkey_client, job.submission_id)

    assert job.submission_id not in await valkey_client.lrange(QUEUE_AI_REVIEW_PENDING_KEY, 0, -1)
    assert job.submission_id not in await valkey_client.lrange(QUEUE_AI_REVIEW_INFLIGHT_KEY, 0, -1)
    assert await valkey_client.zscore(QUEUE_AI_REVIEW_INFLIGHT_TIMES_KEY, job.submission_id) is None
    assert await valkey_client.exists(f"{QUEUE_AI_REVIEW_JOB_HASH_PREFIX}:{job.submission_id}") == 0


async def test_complete_arena_ai_review_job_is_idempotent(
    valkey_client: aivalkey.Valkey,
) -> None:
    """Terminal cleanup succeeds when the job state is already absent."""
    submission_id = str(uuid4())

    await complete_arena_ai_review_job(valkey_client, submission_id)
    await complete_arena_ai_review_job(valkey_client, submission_id)


# ---------------------------------------------------------------------------
# ValkeyRuntime — buffering on write failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_buffers_enqueue_ai_review_job_when_write_fails() -> None:
    """ValkeyRuntime buffers the command and marks itself unavailable on a write error."""
    runtime = ValkeyRuntime(valkey_url="redis://localhost:6379/15", healthcheck_interval_s=60)
    runtime._client = _FakeValkey(fail_writes=True)  # type: ignore[assignment]
    runtime._is_available = True

    job = ArenaAIReviewJob(
        submission_id=str(uuid4()),
        user_id=str(uuid4()),
        problem_id=str(uuid4()),
        language_id="gcc-c17",
    )
    await runtime.enqueue_arena_ai_review_job(job)

    assert runtime.pending_count == 1
    assert runtime.is_available is False


@pytest.mark.asyncio
async def test_runtime_flushes_buffered_ai_review_job_after_recovery() -> None:
    """Buffered enqueue_arena_ai_review_job is replayed when Valkey becomes reachable."""
    runtime = ValkeyRuntime(valkey_url="redis://localhost:6379/15", healthcheck_interval_s=60)
    failing_client = _FakeValkey(fail_writes=True)
    runtime._client = failing_client  # type: ignore[assignment]

    job = ArenaAIReviewJob(
        submission_id=str(uuid4()),
        user_id=str(uuid4()),
        problem_id=str(uuid4()),
        language_id="gcc-c17",
    )
    await runtime.enqueue_arena_ai_review_job(job)
    assert runtime.pending_count == 1

    healthy_client = _FakeValkey(fail_writes=False)
    runtime._client = healthy_client  # type: ignore[assignment]
    runtime._is_available = True
    async with runtime._reconnect_lock:
        await runtime._flush_pending_commands_locked()

    assert runtime.pending_count == 0
    assert any(cmd[0] == "hset" for cmd in healthy_client.executed)
    assert any(cmd[0] == "lpush" for cmd in healthy_client.executed)


@pytest.mark.asyncio
async def test_runtime_buffers_and_replays_ai_review_completion() -> None:
    """Terminal cleanup is buffered during an outage and replayed after recovery."""
    runtime = ValkeyRuntime(valkey_url="redis://localhost:6379/15", healthcheck_interval_s=60)
    runtime._client = _FakeValkey(fail_writes=True)  # type: ignore[assignment]
    runtime._is_available = True

    await runtime.complete_arena_ai_review_job(str(uuid4()))

    assert runtime.pending_count == 1
    assert runtime.is_available is False

    healthy_client = _FakeValkey(fail_writes=False)
    runtime._client = healthy_client  # type: ignore[assignment]
    runtime._is_available = True
    async with runtime._reconnect_lock:
        await runtime._flush_pending_commands_locked()

    assert runtime.pending_count == 0
    assert [command[0] for command in healthy_client.executed] == ["lrem", "lrem", "zrem", "delete"]
