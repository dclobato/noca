#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for the health monitor uptime statistics service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from healthmonitor.services.service_registry import MONITORED_SERVICES
from healthmonitor.services.uptime_stats import (
    SLOT_SECONDS,
    SLOTS_PER_WINDOW,
    SlotStat,
    read_service_heatmap,
    reap_expired_slots,
    record_probe,
    slot_epoch,
    stats_key,
)
from shared.services.valkey_service import WorkerClass


class FakeValkeyRuntime:
    """Minimal stand-in implementing the runtime methods the service uses."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, int]] = {}
        self.ttls: dict[str, int] = {}
        self.deleted: list[str] = []

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        key, up_flag, ttl = args
        bucket = self.hashes.setdefault(key, {"up": 0, "total": 0})
        bucket["total"] += 1
        if up_flag == "1":
            bucket["up"] += 1
        self.ttls[key] = int(ttl)
        return 1

    async def hmget(self, key: str, fields: list[str]) -> list[str | None]:
        bucket = self.hashes.get(key)
        if bucket is None:
            return [None] * len(fields)
        return [str(bucket[field]) if field in bucket else None for field in fields]

    async def delete(self, *keys: str) -> None:
        self.deleted.extend(keys)
        for key in keys:
            self.hashes.pop(key, None)


def test_slot_epoch_snaps_to_12h_utc_boundaries() -> None:
    """Slots start at UTC midnight and noon regardless of the probe time."""
    morning = datetime(2026, 7, 16, 8, 30, tzinfo=UTC)
    afternoon = datetime(2026, 7, 16, 13, 0, tzinfo=UTC)
    assert slot_epoch(morning) == int(datetime(2026, 7, 16, 0, 0, tzinfo=UTC).timestamp())
    assert slot_epoch(afternoon) == int(datetime(2026, 7, 16, 12, 0, tzinfo=UTC).timestamp())
    assert slot_epoch(afternoon) - slot_epoch(morning) == SLOT_SECONDS


def test_stats_key_naming() -> None:
    """Keys carry the noca:healthmon prefix, class value and slot epoch."""
    assert stats_key(WorkerClass.WEB, 1784203200) == "noca:healthmon:stats:web:1784203200"


def test_slot_stat_uptime_pct() -> None:
    """The percentage derives from up/total; empty slots report None."""
    when = datetime(2026, 7, 16, tzinfo=UTC)
    assert SlotStat(when, up=144, total=144).uptime_pct == 100.0
    assert SlotStat(when, up=72, total=144).uptime_pct == 50.0
    assert SlotStat(when, up=0, total=0).uptime_pct is None


@pytest.mark.asyncio
async def test_record_probe_counts_and_ttl() -> None:
    """Probes increment total always, up only on success, and set the TTL."""
    fake: Any = FakeValkeyRuntime()
    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    await record_probe(fake, WorkerClass.RATING, up=True, retention_days=30, now=now)
    await record_probe(fake, WorkerClass.RATING, up=False, retention_days=30, now=now)
    key = stats_key(WorkerClass.RATING, slot_epoch(now))
    assert fake.hashes[key] == {"up": 1, "total": 2}
    assert fake.ttls[key] == 31 * 86_400


@pytest.mark.asyncio
async def test_read_service_heatmap_returns_60_slots_oldest_first() -> None:
    """The heatmap covers exactly the visible window, oldest slot first."""
    fake: Any = FakeValkeyRuntime()
    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    await record_probe(fake, WorkerClass.WEB, up=True, retention_days=30, now=now)
    slots = await read_service_heatmap(fake, WorkerClass.WEB, now=now)
    assert len(slots) == SLOTS_PER_WINDOW
    assert slots[0].slot_start < slots[-1].slot_start
    assert slots[-1].uptime_pct == 100.0
    assert all(slot.uptime_pct is None for slot in slots[:-1])


@pytest.mark.asyncio
async def test_reap_expired_slots_deletes_only_pre_window_keys() -> None:
    """The reaper targets the window right before the visible one, per service."""
    fake: Any = FakeValkeyRuntime()
    now = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)
    newest = slot_epoch(now)
    oldest_visible = newest - (SLOTS_PER_WINDOW - 1) * SLOT_SECONDS
    deleted_count = await reap_expired_slots(fake, MONITORED_SERVICES, now=now)
    assert deleted_count == len(MONITORED_SERVICES) * SLOTS_PER_WINDOW
    visible_keys = {
        stats_key(service.worker_class, newest - index * SLOT_SECONDS)
        for service in MONITORED_SERVICES
        for index in range(SLOTS_PER_WINDOW)
    }
    assert not visible_keys.intersection(fake.deleted)
    assert stats_key(MONITORED_SERVICES[0].worker_class, oldest_visible - SLOT_SECONDS) in fake.deleted
