#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit and real-Valkey integration tests for worker presence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from shared.services.valkey_service.worker_presence import (
    WorkerClass,
    list_workers,
    publish_worker_last_job,
    publish_worker_presence,
    remove_worker,
    resolve_worker_id,
    worker_last_jobs_key,
    worker_live_key,
    worker_presence_loop,
    worker_registry_key,
)


@pytest.mark.asyncio
async def test_presence_round_trip_against_real_valkey_db_15(valkey_client: Any) -> None:
    """Persist history, report online state, and preserve start time on refresh."""
    started_at = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
    observed_at = datetime(2026, 6, 11, 12, 5, tzinfo=UTC)

    await publish_worker_presence(
        valkey_client,
        worker_class=WorkerClass.AUTOJUDGE,
        worker_id="judge-1",
        started_at=started_at,
        ttl_seconds=60,
        observed_at=observed_at,
    )

    first_ttl = await valkey_client.ttl(worker_live_key(WorkerClass.AUTOJUDGE, "judge-1"))
    assert 0 < first_ttl <= 60
    registry_value = await valkey_client.hget(
        worker_registry_key(WorkerClass.AUTOJUDGE),
        "judge-1",
    )
    assert registry_value is not None
    assert '"started_at":"2026-06-11T12:00:00+00:00"' in registry_value
    assert '"last_seen_at":"2026-06-11T12:05:00+00:00"' in registry_value

    rows = await list_workers(valkey_client, WorkerClass.AUTOJUDGE)
    assert len(rows) == 1
    assert rows[0].worker_id == "judge-1"
    assert rows[0].started_at == started_at
    assert rows[0].last_seen_at == observed_at
    assert rows[0].online is True

    await publish_worker_presence(
        valkey_client,
        worker_class=WorkerClass.AUTOJUDGE,
        worker_id="judge-1",
        started_at=started_at,
        ttl_seconds=60,
        observed_at=observed_at,
    )
    assert (await list_workers(valkey_client, WorkerClass.AUTOJUDGE))[0].started_at == started_at


@pytest.mark.asyncio
async def test_offline_remove_and_reappearance_against_real_valkey_db_15(valkey_client: Any) -> None:
    """Keep expired workers visible, remove them, and recreate them on heartbeat."""
    started_at = datetime(2026, 6, 11, 13, 0, tzinfo=UTC)
    worker_class = WorkerClass.RATING
    worker_id = "rating-1"

    await publish_worker_presence(
        valkey_client,
        worker_class=worker_class,
        worker_id=worker_id,
        started_at=started_at,
        ttl_seconds=60,
    )
    await valkey_client.expire(worker_live_key(worker_class, worker_id), 1)
    await asyncio.sleep(1.1)

    rows = await list_workers(valkey_client, worker_class)
    assert len(rows) == 1
    assert rows[0].online is False
    assert rows[0].last_seen_at >= started_at

    await remove_worker(valkey_client, worker_class=worker_class, worker_id=worker_id)
    assert await list_workers(valkey_client, worker_class) == []

    await publish_worker_presence(
        valkey_client,
        worker_class=worker_class,
        worker_id=worker_id,
        started_at=started_at,
        ttl_seconds=60,
    )
    assert (await list_workers(valkey_client, worker_class))[0].online is True


@pytest.mark.asyncio
async def test_worker_classes_and_ids_are_isolated_against_real_valkey_db_15(valkey_client: Any) -> None:
    """Keep identical IDs in separate worker-class registries."""
    started_at = datetime(2026, 6, 11, 14, 0, tzinfo=UTC)
    for worker_class in WorkerClass:
        await publish_worker_presence(
            valkey_client,
            worker_class=worker_class,
            worker_id="shared-id",
            started_at=started_at,
            ttl_seconds=60,
        )

    for worker_class in WorkerClass:
        rows = await list_workers(valkey_client, worker_class)
        assert [(row.worker_id, row.online) for row in rows] == [("shared-id", True)]


@pytest.mark.asyncio
async def test_presence_loop_publishes_immediately_and_stops() -> None:
    """Publish immediately, repeat on schedule, and retain one start timestamp."""
    stop_event = asyncio.Event()
    client = _LoopClient(stop_event, stop_after=2)
    started_at = datetime(2026, 6, 11, 15, 0, tzinfo=UTC)

    await worker_presence_loop(
        client,
        worker_class=WorkerClass.AIASSISTANT,
        worker_id="ai-1",
        started_at=started_at,
        interval_seconds=0.01,
        ttl_seconds=60,
        stop_event=stop_event,
    )

    assert len(client.eval_args) == 2
    payloads = [args[3] for args in client.eval_args]
    assert all(f'"started_at":"{started_at.isoformat()}"' in payload for payload in payloads)


def test_resolve_worker_id_uses_configured_value_or_process_fallback() -> None:
    """Prefer configured IDs and otherwise derive a host/process identity."""
    assert resolve_worker_id("  worker-7  ") == "worker-7"
    assert ":" in resolve_worker_id("")


@pytest.mark.asyncio
async def test_publish_last_job_stores_timestamp_against_real_valkey_db_15(valkey_client: Any) -> None:
    """publish_worker_last_job writes an ISO timestamp into the per-class hash."""
    started_at = datetime(2026, 6, 11, 15, 0, tzinfo=UTC)
    await publish_worker_presence(
        valkey_client,
        worker_class=WorkerClass.AUTOJUDGE,
        worker_id="judge-1",
        started_at=started_at,
        ttl_seconds=60,
    )
    await publish_worker_last_job(
        valkey_client,
        worker_class=WorkerClass.AUTOJUDGE,
        worker_id="judge-1",
        started_at=started_at,
    )

    raw = await valkey_client.hget(worker_last_jobs_key(WorkerClass.AUTOJUDGE), "judge-1")
    assert raw is not None
    assert "2026-06-11T15:00:00" in raw

    rows = await list_workers(valkey_client, WorkerClass.AUTOJUDGE)
    assert len(rows) == 1
    assert rows[0].last_job_at == started_at


@pytest.mark.asyncio
async def test_last_job_absent_returns_none_against_real_valkey_db_15(valkey_client: Any) -> None:
    """list_workers returns last_job_at=None when no last-job entry exists."""
    started_at = datetime(2026, 6, 11, 15, 0, tzinfo=UTC)
    await publish_worker_presence(
        valkey_client,
        worker_class=WorkerClass.AUTOJUDGE,
        worker_id="judge-nojob",
        started_at=started_at,
        ttl_seconds=60,
    )

    rows = await list_workers(valkey_client, WorkerClass.AUTOJUDGE)
    assert len(rows) == 1
    assert rows[0].last_job_at is None


@pytest.mark.asyncio
async def test_last_job_isolated_by_worker_class_against_real_valkey_db_15(valkey_client: Any) -> None:
    """publish_worker_last_job only updates the correct worker class hash."""
    started_at = datetime(2026, 6, 11, 15, 0, tzinfo=UTC)
    for worker_class in WorkerClass:
        await publish_worker_presence(
            valkey_client,
            worker_class=worker_class,
            worker_id="shared-id",
            started_at=started_at,
            ttl_seconds=60,
        )

    # Only publish last job for AUTOJUDGE
    await publish_worker_last_job(
        valkey_client,
        worker_class=WorkerClass.AUTOJUDGE,
        worker_id="shared-id",
        started_at=started_at,
    )

    autojudge_rows = await list_workers(valkey_client, WorkerClass.AUTOJUDGE)
    rating_rows = await list_workers(valkey_client, WorkerClass.RATING)
    assert autojudge_rows[0].last_job_at == started_at
    assert rating_rows[0].last_job_at is None


@pytest.mark.asyncio
async def test_remove_worker_clears_last_job_entry_against_real_valkey_db_15(valkey_client: Any) -> None:
    """remove_worker deletes the last-job hash field alongside the registry entry."""
    started_at = datetime(2026, 6, 11, 15, 0, tzinfo=UTC)
    await publish_worker_presence(
        valkey_client,
        worker_class=WorkerClass.RATING,
        worker_id="rating-1",
        started_at=started_at,
        ttl_seconds=60,
    )
    await publish_worker_last_job(
        valkey_client,
        worker_class=WorkerClass.RATING,
        worker_id="rating-1",
        started_at=started_at,
    )

    await remove_worker(valkey_client, worker_class=WorkerClass.RATING, worker_id="rating-1")

    assert await list_workers(valkey_client, WorkerClass.RATING) == []
    raw = await valkey_client.hget(worker_last_jobs_key(WorkerClass.RATING), "rating-1")
    assert raw is None


class _LoopClient:
    """Minimal client that stops the loop after its first heartbeat."""

    def __init__(self, stop_event: asyncio.Event, *, stop_after: int) -> None:
        self.stop_event = stop_event
        self.stop_after = stop_after
        self.eval_args: list[tuple[str, ...]] = []
        self.hashes: dict[str, dict[str, str]] = {}

    async def eval(self, script: str, numkeys: int, *args: str) -> object:
        """Record heartbeats and request shutdown at the configured count."""
        self.eval_args.append(args)
        if len(self.eval_args) >= self.stop_after:
            self.stop_event.set()
        return 1

    async def hgetall(self, key: str) -> dict[str, str]:
        """Return an empty registry."""
        return {}

    async def hset(self, key: str, field: str, value: str) -> None:
        """Store a hash field."""
        self.hashes.setdefault(key, {})[field] = value

    async def hmget(self, key: str, fields: list[str]) -> list[str | None]:
        """Fetch hash fields."""
        h = self.hashes.get(key, {})
        return [h.get(f) for f in fields]

    async def mget(self, keys: list[str]) -> list[str | None]:
        """Return no live values."""
        return [None for _key in keys]

    async def delete(self, *keys: str) -> int:
        """Pretend keys were deleted."""
        return len(keys)
