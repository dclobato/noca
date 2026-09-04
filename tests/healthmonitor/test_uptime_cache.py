#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for the per-process uptime payload cache."""

from __future__ import annotations

import asyncio

import pytest

from healthmonitor.services.uptime_cache import UptimeHistoryCache


class _Clock:
    """Manually advanced monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Builder:
    """Counting build coroutine with an optional gate to hold concurrent callers."""

    def __init__(self) -> None:
        self.calls = 0
        self.gate = asyncio.Event()
        self.gate.set()

    async def __call__(self) -> str:
        self.calls += 1
        await self.gate.wait()
        return f"payload-{self.calls}"


@pytest.mark.asyncio
async def test_hit_returns_cached_value_with_remaining_ttl() -> None:
    """A second call inside the window reuses the value and reports the seconds left."""
    clock = _Clock()
    cache: UptimeHistoryCache[str] = UptimeHistoryCache(ttl_seconds=300, clock=clock)
    builder = _Builder()

    first, first_age = await cache.get(builder)
    clock.now += 100.5
    second, second_age = await cache.get(builder)

    assert first == second == "payload-1"
    assert first_age == 300
    assert second_age == 200
    assert builder.calls == 1


@pytest.mark.asyncio
async def test_expiry_rebuilds_once() -> None:
    """Past the TTL the next call rebuilds; the old value is never served again."""
    clock = _Clock()
    cache: UptimeHistoryCache[str] = UptimeHistoryCache(ttl_seconds=60, clock=clock)
    builder = _Builder()

    await cache.get(builder)
    clock.now += 60
    value, age = await cache.get(builder)

    assert value == "payload-2"
    assert age == 60
    assert builder.calls == 2


@pytest.mark.asyncio
async def test_concurrent_misses_build_once() -> None:
    """Callers racing on an empty cache share one build instead of stampeding."""
    cache: UptimeHistoryCache[str] = UptimeHistoryCache(ttl_seconds=60, clock=_Clock())
    builder = _Builder()
    builder.gate.clear()

    tasks = [asyncio.create_task(cache.get(builder)) for _ in range(5)]
    await asyncio.sleep(0)
    builder.gate.set()
    results = await asyncio.gather(*tasks)

    assert builder.calls == 1
    assert {value for value, _ in results} == {"payload-1"}


@pytest.mark.asyncio
async def test_failed_build_caches_nothing() -> None:
    """A build that raises propagates and leaves the cache empty for the next caller."""
    cache: UptimeHistoryCache[str] = UptimeHistoryCache(ttl_seconds=60, clock=_Clock())
    attempts = 0

    async def _failing() -> str:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("valkey down")

    with pytest.raises(RuntimeError):
        await cache.get(_failing)
    with pytest.raises(RuntimeError):
        await cache.get(_failing)

    assert attempts == 2


@pytest.mark.asyncio
async def test_invalidate_forces_rebuild() -> None:
    """Invalidating inside the window makes the next call rebuild immediately."""
    cache: UptimeHistoryCache[str] = UptimeHistoryCache(ttl_seconds=60, clock=_Clock())
    builder = _Builder()

    await cache.get(builder)
    cache.invalidate()
    value, _ = await cache.get(builder)

    assert value == "payload-2"
    assert builder.calls == 2


def test_ttl_floor_is_one_second() -> None:
    """A non-positive TTL is clamped so ``max-age`` never becomes zero or negative."""
    assert UptimeHistoryCache(ttl_seconds=0).ttl_seconds == 1
