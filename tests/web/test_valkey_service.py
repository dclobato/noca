from __future__ import annotations

import json
from uuid import uuid4

import pytest
import valkey.asyncio as aivalkey
from pydantic import ValidationError
from valkey.exceptions import ConnectionError as ValkeyConnectionError
from valkey.exceptions import ResponseError

from shared.enumerations import Verdict
from shared.queue_schema import JudgeJob, ProfilingJob, VerdictEvent
from web.config import settings
from web.services.valkey_service import (
    ValkeyRuntime,
    create_valkey_pool,
    dequeue_job_id,
    enqueue_job,
    enqueue_profiling_job,
    get_contest_queue_metrics,
    publish_verdict,
    remove_from_inflight,
)


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

    async def execute(self) -> None:
        if self.client.fail_writes:
            raise ValkeyConnectionError("valkey unavailable")
        self.client.executed.extend(self.commands)


class _FakeValkey:
    def __init__(self, *, fail_writes: bool = False) -> None:
        self.fail_writes = fail_writes
        self.executed: list[tuple[str, object, object | None]] = []

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    async def publish(self, channel: str, payload: str) -> None:
        if self.fail_writes:
            raise ValkeyConnectionError("valkey unavailable")
        self.executed.append(("publish", channel, payload))

    async def ping(self) -> bool:
        if self.fail_writes:
            raise ValkeyConnectionError("valkey unavailable")
        return True

    async def aclose(self) -> None:
        return None


@pytest.mark.real_valkey
async def test_create_valkey_pool_connects_to_real_valkey() -> None:
    pool = create_valkey_pool(settings.valkey_url)
    client = aivalkey.Valkey.from_pool(pool)

    try:
        assert pool.connection_kwargs["host"] == settings.VALKEY_SERVER
        assert pool.connection_kwargs["port"] == settings.VALKEY_PORT
        assert pool.connection_kwargs["db"] == 15
        try:
            assert await client.ping() is True
        except (ResponseError, ValkeyConnectionError) as exc:
            pytest.skip(f"Valkey at {settings.valkey_url} is unavailable for tests: {exc}")
    finally:
        await client.aclose()
        await pool.aclose()


def test_judge_job_requires_contest_id() -> None:
    with pytest.raises(ValidationError):
        JudgeJob(judgment_id=str(uuid4()), is_rejudge=False, requeue_count=0)


def test_profiling_job_requires_contest_id() -> None:
    with pytest.raises(ValidationError):
        ProfilingJob(profiling_run_id=str(uuid4()), problem_id=str(uuid4()), language_id=str(uuid4()))


async def test_enqueue_job_stores_hash_and_pushes_to_selected_queue(valkey_client: aivalkey.Valkey) -> None:
    normal_job = JudgeJob(
        judgment_id=str(uuid4()),
        contest_id=str(uuid4()),
        is_rejudge=False,
        requeue_count=2,
    )
    priority_job = JudgeJob(
        judgment_id=str(uuid4()),
        contest_id=str(uuid4()),
        submission_id=str(uuid4()),
        is_rejudge=True,
        requeue_count=5,
    )

    await enqueue_job(valkey_client, normal_job, priority=False)
    await enqueue_job(valkey_client, priority_job, priority=True)

    normal_hash = await valkey_client.hgetall(f"{settings.queue_job_hash_prefix}:{normal_job.judgment_id}")
    priority_hash = await valkey_client.hgetall(f"{settings.queue_job_hash_prefix}:{priority_job.judgment_id}")

    assert normal_hash == {
        "judgment_id": normal_job.judgment_id,
        "contest_id": normal_job.contest_id,
        "is_rejudge": "false",
        "requeue_count": "2",
        "job_kind": "submission",
    }
    assert priority_hash == {
        "judgment_id": priority_job.judgment_id,
        "contest_id": priority_job.contest_id,
        "submission_id": priority_job.submission_id,
        "is_rejudge": "true",
        "requeue_count": "5",
        "job_kind": "submission",
    }
    assert await valkey_client.lrange(settings.queue_pending_key, 0, -1) == [normal_job.judgment_id]
    assert await valkey_client.lrange(settings.queue_priority_key, 0, -1) == [priority_job.judgment_id]


async def test_enqueue_profiling_job_stores_hash_and_pushes_to_profiling_queue(
    valkey_client: aivalkey.Valkey,
) -> None:
    job = ProfilingJob(
        profiling_run_id=str(uuid4()),
        contest_id=str(uuid4()),
        problem_id=str(uuid4()),
        language_id=str(uuid4()),
        requeue_count=3,
    )

    await enqueue_profiling_job(valkey_client, job)

    stored_hash = await valkey_client.hgetall(f"{settings.queue_job_hash_prefix}:{job.profiling_run_id}")

    assert stored_hash == {
        "profiling_run_id": job.profiling_run_id,
        "contest_id": job.contest_id,
        "problem_id": job.problem_id,
        "language_id": job.language_id,
        "requeue_count": "3",
        "job_kind": "profiling",
    }
    assert await valkey_client.lrange(settings.queue_profiling_key, 0, -1) == [job.profiling_run_id]


async def test_dequeue_job_id_prefers_priority_then_moves_to_inflight(valkey_client: aivalkey.Valkey) -> None:
    pending_job_id = str(uuid4())
    priority_job_id = str(uuid4())

    await valkey_client.lpush(settings.queue_pending_key, pending_job_id)
    await valkey_client.lpush(settings.queue_priority_key, priority_job_id)

    first = await dequeue_job_id(valkey_client)
    second = await dequeue_job_id(valkey_client)
    third = await dequeue_job_id(valkey_client)

    assert first == priority_job_id
    assert second == pending_job_id
    assert third is None
    assert await valkey_client.lrange(settings.queue_priority_key, 0, -1) == []
    assert await valkey_client.lrange(settings.queue_pending_key, 0, -1) == []
    assert await valkey_client.lrange(settings.queue_inflight_key, 0, -1) == [pending_job_id, priority_job_id]


async def test_get_contest_queue_metrics_counts_target_contest_only(valkey_client: aivalkey.Valkey) -> None:
    target_contest = str(uuid4())
    other_contest = str(uuid4())

    target_profiling_id = str(uuid4())
    target_priority_id = str(uuid4())
    target_inflight_id = str(uuid4())
    other_pending_id = str(uuid4())
    legacy_pending_id = str(uuid4())

    await valkey_client.lpush(settings.queue_profiling_key, target_profiling_id)
    await valkey_client.lpush(settings.queue_priority_key, target_priority_id)
    await valkey_client.lpush(settings.queue_inflight_key, target_inflight_id)
    await valkey_client.lpush(settings.queue_pending_key, other_pending_id)
    await valkey_client.lpush(settings.queue_pending_key, legacy_pending_id)

    await valkey_client.hset(
        f"{settings.queue_job_hash_prefix}:{target_profiling_id}",
        mapping={"profiling_run_id": target_profiling_id, "contest_id": target_contest},
    )
    await valkey_client.hset(
        f"{settings.queue_job_hash_prefix}:{target_priority_id}",
        mapping={"judgment_id": target_priority_id, "contest_id": target_contest},
    )
    await valkey_client.hset(
        f"{settings.queue_job_hash_prefix}:{target_inflight_id}",
        mapping={"judgment_id": target_inflight_id, "contest_id": target_contest},
    )
    await valkey_client.hset(
        f"{settings.queue_job_hash_prefix}:{other_pending_id}",
        mapping={"judgment_id": other_pending_id, "contest_id": other_contest},
    )
    await valkey_client.hset(
        f"{settings.queue_job_hash_prefix}:{legacy_pending_id}",
        mapping={"judgment_id": legacy_pending_id},
    )

    metrics = await get_contest_queue_metrics(valkey_client, target_contest)

    assert metrics is not None
    assert metrics.contest_id == target_contest
    assert metrics.profiling_count == 1
    assert metrics.priority_count == 1
    assert metrics.pending_count == 0
    assert metrics.inflight_count == 1
    assert metrics.total_count == 3
    assert metrics.unknown_contest_seen == 1
    assert metrics.scanned_queue_items == 5
    assert metrics.metrics_latency_ms >= 0.0


async def test_remove_from_inflight_cleans_list_and_sorted_set(valkey_client: aivalkey.Valkey) -> None:
    judgment_id = str(uuid4())

    await valkey_client.lpush(settings.queue_inflight_key, judgment_id)
    await valkey_client.zadd(settings.queue_inflight_times_key, {judgment_id: 123.0})

    await remove_from_inflight(valkey_client, judgment_id)

    assert await valkey_client.lrange(settings.queue_inflight_key, 0, -1) == []
    assert await valkey_client.zrange(settings.queue_inflight_times_key, 0, -1) == []


async def test_publish_verdict_emits_json_payload_to_results_channel(valkey_client: aivalkey.Valkey) -> None:
    pubsub = valkey_client.pubsub()
    await pubsub.subscribe(settings.queue_results_channel)
    await pubsub.get_message(ignore_subscribe_messages=False, timeout=1.0)

    event = VerdictEvent(
        submission_id=str(uuid4()),
        judgment_id=str(uuid4()),
        verdict=Verdict.AC.value,
        contest_id=str(uuid4()),
        compile_log="ok",
        score=100.0,
        judge_time_ms=73,
        update_kind="confirmation",
        team_id=str(uuid4()),
        problem_id=str(uuid4()),
    )

    try:
        await publish_verdict(valkey_client, event)
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
    finally:
        await pubsub.unsubscribe(settings.queue_results_channel)
        await pubsub.aclose()

    assert message is not None
    assert message["type"] == "message"
    payload = message["data"]
    if isinstance(payload, bytes):
        payload = payload.decode()
    assert json.loads(payload) == event.model_dump(mode="json")


@pytest.mark.asyncio
async def test_runtime_buffers_enqueue_command_when_write_fails() -> None:
    runtime = ValkeyRuntime(valkey_url="redis://localhost:6379/15", healthcheck_interval_s=60)
    runtime._client = _FakeValkey(fail_writes=True)  # type: ignore[assignment]
    runtime._is_available = True

    job = JudgeJob(judgment_id=str(uuid4()), contest_id=str(uuid4()), is_rejudge=False, requeue_count=0)
    await runtime.enqueue_job(job, priority=False)

    assert runtime.pending_count == 1
    assert runtime.is_available is False


@pytest.mark.asyncio
async def test_runtime_flushes_buffered_commands_after_recovery() -> None:
    runtime = ValkeyRuntime(valkey_url="redis://localhost:6379/15", healthcheck_interval_s=60)
    failing_client = _FakeValkey(fail_writes=True)
    runtime._client = failing_client  # type: ignore[assignment]

    job = JudgeJob(judgment_id=str(uuid4()), contest_id=str(uuid4()), is_rejudge=False, requeue_count=0)
    await runtime.enqueue_job(job, priority=False)
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
async def test_runtime_get_contest_queue_metrics_returns_none_on_recoverable_error(monkeypatch) -> None:
    runtime = ValkeyRuntime(valkey_url="redis://localhost:6379/15", healthcheck_interval_s=60)
    runtime._client = _FakeValkey(fail_writes=False)  # type: ignore[assignment]
    runtime._is_available = True

    reconnect_called = False

    def _mark_reconnect() -> None:
        nonlocal reconnect_called
        reconnect_called = True

    async def _raise_recoverable(client: object, contest_id: str) -> object:
        raise ValkeyConnectionError("valkey unavailable")

    monkeypatch.setattr("shared.services.valkey_service._get_contest_queue_metrics_with_client", _raise_recoverable)
    monkeypatch.setattr(runtime, "_schedule_reconnect", _mark_reconnect)

    result = await runtime.get_contest_queue_metrics(str(uuid4()))

    assert result is None
    assert runtime.is_available is False
    assert reconnect_called is True


async def test_runtime_get_contest_queue_metrics_happy_path(valkey_client: aivalkey.Valkey) -> None:
    """ValkeyRuntime.get_contest_queue_metrics returns correct counts via a real Valkey connection."""
    target_contest = str(uuid4())
    other_contest = str(uuid4())

    target_profiling_id = str(uuid4())
    target_priority_id = str(uuid4())
    target_inflight_id = str(uuid4())
    other_pending_id = str(uuid4())

    await valkey_client.lpush(settings.queue_profiling_key, target_profiling_id)
    await valkey_client.lpush(settings.queue_priority_key, target_priority_id)
    await valkey_client.lpush(settings.queue_inflight_key, target_inflight_id)
    await valkey_client.lpush(settings.queue_pending_key, other_pending_id)

    await valkey_client.hset(
        f"{settings.queue_job_hash_prefix}:{target_profiling_id}",
        mapping={"profiling_run_id": target_profiling_id, "contest_id": target_contest},
    )
    await valkey_client.hset(
        f"{settings.queue_job_hash_prefix}:{target_priority_id}",
        mapping={"judgment_id": target_priority_id, "contest_id": target_contest},
    )
    await valkey_client.hset(
        f"{settings.queue_job_hash_prefix}:{target_inflight_id}",
        mapping={"judgment_id": target_inflight_id, "contest_id": target_contest},
    )
    await valkey_client.hset(
        f"{settings.queue_job_hash_prefix}:{other_pending_id}",
        mapping={"judgment_id": other_pending_id, "contest_id": other_contest},
    )

    runtime = ValkeyRuntime(valkey_url=settings.valkey_url, healthcheck_interval_s=60)
    try:
        await runtime.start()
        metrics = await runtime.get_contest_queue_metrics(target_contest)
    finally:
        await runtime.stop()

    assert metrics is not None
    assert metrics.contest_id == target_contest
    assert metrics.profiling_count == 1
    assert metrics.priority_count == 1
    assert metrics.pending_count == 0
    assert metrics.inflight_count == 1
    assert metrics.total_count == 3
    assert metrics.unknown_contest_seen == 0
    assert metrics.scanned_queue_items == 4
    assert metrics.metrics_latency_ms >= 0.0
