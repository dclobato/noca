#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for the health monitor presence probe."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from healthmonitor.services.presence_probe import (
    ServiceState,
    read_service_statuses,
    unknown_service_statuses,
)
from healthmonitor.services.service_registry import MONITORED_SERVICES
from shared.services.valkey_service import WorkerClass
from shared.services.valkey_service.worker_presence import worker_live_key, worker_registry_key


class FakePresenceRuntime:
    """Fake exposing the reads list_workers performs, plus is_available."""

    def __init__(
        self,
        *,
        online: set[WorkerClass],
        seen: set[WorkerClass],
        available: bool = True,
        replicas: int = 1,
    ) -> None:
        self.is_available = available
        worker_ids = [f"w-{index}" for index in range(1, replicas + 1)]
        payload = {
            "started_at": datetime(2026, 7, 16, tzinfo=UTC).isoformat(),
            "last_seen_at": datetime(2026, 7, 16, tzinfo=UTC).isoformat(),
        }
        payloads = {worker_id: json.dumps({"worker_id": worker_id, **payload}) for worker_id in worker_ids}
        self._registry = {worker_registry_key(wc): dict(payloads) for wc in seen}
        self._live = {worker_live_key(wc, worker_id): payloads[worker_id] for wc in online for worker_id in worker_ids}

    async def hgetall(self, key: str) -> dict[str, str]:
        return self._registry.get(key, {})

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self._live.get(key) for key in keys]

    async def hmget(self, key: str, fields: list[str]) -> list[str | None]:
        return [None] * len(fields)


@pytest.mark.asyncio
async def test_online_worker_marks_service_available() -> None:
    """Any live replica makes the service available; seen-but-expired does not."""
    fake: Any = FakePresenceRuntime(
        online={WorkerClass.WEB},
        seen={WorkerClass.WEB, WorkerClass.RATING},
    )
    statuses = await read_service_statuses(fake)
    by_class = {status.service.worker_class: status for status in statuses}
    assert by_class[WorkerClass.WEB].state is ServiceState.AVAILABLE
    assert by_class[WorkerClass.WEB].online_count == 1
    assert by_class[WorkerClass.RATING].state is ServiceState.UNAVAILABLE
    assert by_class[WorkerClass.RATING].online_count == 0
    assert by_class[WorkerClass.ARENA].state is ServiceState.UNAVAILABLE


@pytest.mark.asyncio
async def test_online_count_reflects_all_live_replicas() -> None:
    """Every live replica of a class is counted, not just the first."""
    fake: Any = FakePresenceRuntime(
        online={WorkerClass.WEB},
        seen={WorkerClass.WEB},
        replicas=3,
    )
    statuses = await read_service_statuses(fake)
    by_class = {status.service.worker_class: status for status in statuses}
    assert by_class[WorkerClass.WEB].state is ServiceState.AVAILABLE
    assert by_class[WorkerClass.WEB].online_count == 3


@pytest.mark.asyncio
async def test_valkey_outage_reports_unknown_for_all_services() -> None:
    """An unreachable Valkey yields unknown, never unavailable."""
    fake: Any = FakePresenceRuntime(online=set(), seen=set(), available=False)
    statuses = await read_service_statuses(fake)
    assert [status.state for status in statuses] == [ServiceState.UNKNOWN] * len(MONITORED_SERVICES)


def test_unknown_service_statuses_covers_registry_order() -> None:
    """The fallback keeps registry order and marks every service unknown."""
    statuses = unknown_service_statuses()
    assert [status.service for status in statuses] == list(MONITORED_SERVICES)
    assert all(status.state is ServiceState.UNKNOWN for status in statuses)
