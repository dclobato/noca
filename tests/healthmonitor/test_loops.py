#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for the health monitor background loops."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from healthmonitor.services import loops


@pytest.fixture
def stop_after_one_pass(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    """Stop the reaper loop as soon as it starts waiting for the next pass."""
    stop_event = asyncio.Event()

    async def _stop_and_wait(awaitable: Any, timeout: float) -> None:
        awaitable.close()
        stop_event.set()
        raise TimeoutError

    monkeypatch.setattr(loops.asyncio, "wait_for", _stop_and_wait)
    return stop_event


@pytest.mark.asyncio
async def test_reaper_pass_runs_both_cleanups(
    monkeypatch: pytest.MonkeyPatch,
    stop_after_one_pass: asyncio.Event,
) -> None:
    """One pass reaps expired uptime slots and prunes stale presence records."""
    calls: list[str] = []

    async def _reap(runtime: Any, services: Any) -> int:
        calls.append("slots")
        return 3

    async def _prune(runtime: Any) -> int:
        calls.append("presence")
        return 2

    monkeypatch.setattr(loops, "reap_expired_slots", _reap)
    monkeypatch.setattr(loops, "prune_all_stale_workers", _prune)

    await loops.run_reaper_loop(object(), stop_after_one_pass, interval_seconds=1)

    assert calls == ["slots", "presence"]


@pytest.mark.asyncio
async def test_failing_slot_reaper_does_not_skip_presence_prune(
    monkeypatch: pytest.MonkeyPatch,
    stop_after_one_pass: asyncio.Event,
) -> None:
    """A failing uptime-slot pass is logged and the presence prune still runs."""
    pruned = False

    async def _reap(runtime: Any, services: Any) -> int:
        raise RuntimeError("valkey down")

    async def _prune(runtime: Any) -> int:
        nonlocal pruned
        pruned = True
        return 0

    monkeypatch.setattr(loops, "reap_expired_slots", _reap)
    monkeypatch.setattr(loops, "prune_all_stale_workers", _prune)

    await loops.run_reaper_loop(object(), stop_after_one_pass, interval_seconds=1)

    assert pruned is True


@pytest.mark.asyncio
async def test_failing_presence_prune_does_not_stop_the_loop(
    monkeypatch: pytest.MonkeyPatch,
    stop_after_one_pass: asyncio.Event,
) -> None:
    """A failing presence prune is logged rather than escaping the loop."""

    async def _reap(runtime: Any, services: Any) -> int:
        return 0

    async def _prune(runtime: Any) -> int:
        raise RuntimeError("valkey down")

    monkeypatch.setattr(loops, "reap_expired_slots", _reap)
    monkeypatch.setattr(loops, "prune_all_stale_workers", _prune)

    await loops.run_reaper_loop(object(), stop_after_one_pass, interval_seconds=1)


@pytest.mark.asyncio
async def test_reaper_loop_does_not_run_when_already_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stop event set before entry skips the pass entirely."""

    async def _fail(*args: Any, **kwargs: Any) -> int:
        raise AssertionError("no cleanup should run")

    monkeypatch.setattr(loops, "reap_expired_slots", _fail)
    monkeypatch.setattr(loops, "prune_all_stale_workers", _fail)

    stop_event = asyncio.Event()
    stop_event.set()

    await loops.run_reaper_loop(object(), stop_event, interval_seconds=1)


@pytest.mark.asyncio
async def test_prober_invalidates_uptime_cache_after_recording(
    monkeypatch: pytest.MonkeyPatch,
    stop_after_one_pass: asyncio.Event,
) -> None:
    """A recorded pass drops the cached ``/uptime.json`` payload."""
    from healthmonitor.services.presence_probe import ServiceState, ServiceStatus
    from healthmonitor.services.service_registry import MONITORED_SERVICES

    recorded: list[str] = []
    invalidations: list[bool] = []

    class _Cache:
        def invalidate(self) -> None:
            invalidations.append(True)

    async def _statuses(runtime: Any) -> list[ServiceStatus]:
        return [ServiceStatus(service=s, state=ServiceState.AVAILABLE, online_count=1) for s in MONITORED_SERVICES]

    async def _record(runtime: Any, worker_class: Any, **kwargs: Any) -> None:
        recorded.append(worker_class.value)

    monkeypatch.setattr(loops, "read_service_statuses", _statuses)
    monkeypatch.setattr(loops, "record_probe", _record)

    await loops.run_prober_loop(
        object(),
        stop_after_one_pass,
        interval_seconds=1,
        retention_days=30,
        uptime_cache=_Cache(),  # type: ignore[arg-type]
    )

    assert len(recorded) == len(MONITORED_SERVICES)
    assert invalidations == [True]


@pytest.mark.asyncio
async def test_prober_skips_invalidation_when_valkey_unreachable(
    monkeypatch: pytest.MonkeyPatch,
    stop_after_one_pass: asyncio.Event,
) -> None:
    """A skipped probe leaves the cache alone -- nothing changed on the Valkey side."""
    from healthmonitor.services.presence_probe import unknown_service_statuses

    invalidations: list[bool] = []

    class _Cache:
        def invalidate(self) -> None:
            invalidations.append(True)

    async def _statuses(runtime: Any) -> Any:
        return unknown_service_statuses()

    monkeypatch.setattr(loops, "read_service_statuses", _statuses)

    await loops.run_prober_loop(
        object(),
        stop_after_one_pass,
        interval_seconds=1,
        retention_days=30,
        uptime_cache=_Cache(),  # type: ignore[arg-type]
    )

    assert invalidations == []
