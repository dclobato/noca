#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for the shared keyed single-flight TTL cache."""

from __future__ import annotations

import asyncio

import pytest

from shared.services.single_flight_cache import SingleFlightCache


class _Clock:
    """Manually advanced monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Builder:
    """Counting build coroutine with an optional gate to hold concurrent callers."""

    def __init__(self, label: str = "payload") -> None:
        self.label = label
        self.calls = 0
        self.gate = asyncio.Event()
        self.gate.set()

    async def __call__(self) -> str:
        self.calls += 1
        await self.gate.wait()
        return f"{self.label}-{self.calls}"


@pytest.mark.asyncio
async def test_hit_returns_cached_value_with_remaining_ttl() -> None:
    clock = _Clock()
    cache: SingleFlightCache[str, str] = SingleFlightCache(clock=clock)
    builder = _Builder()

    first, first_left = await cache.get("k", builder, ttl_seconds=300)
    clock.now += 100.5
    second, second_left = await cache.get("k", builder, ttl_seconds=300)

    assert first == second == "payload-1"
    assert (first_left, second_left) == (300, 200)
    assert builder.calls == 1


@pytest.mark.asyncio
async def test_keys_are_independent_and_expiry_rebuilds() -> None:
    clock = _Clock()
    cache: SingleFlightCache[tuple[str, int], str] = SingleFlightCache(clock=clock)
    a, b = _Builder("a"), _Builder("b")

    await cache.get(("x", 1), a, ttl_seconds=60)
    await cache.get(("x", 2), b, ttl_seconds=60)
    clock.now += 60
    value, left = await cache.get(("x", 1), a, ttl_seconds=60)

    assert (a.calls, b.calls) == (2, 1)
    assert (value, left) == ("a-2", 60)


@pytest.mark.asyncio
async def test_concurrent_misses_on_one_key_build_once() -> None:
    cache: SingleFlightCache[str, str] = SingleFlightCache(clock=_Clock())
    builder = _Builder()
    builder.gate.clear()

    tasks = [asyncio.create_task(cache.get("k", builder, ttl_seconds=60)) for _ in range(5)]
    await asyncio.sleep(0)
    builder.gate.set()
    results = await asyncio.gather(*tasks)

    assert builder.calls == 1
    assert {value for value, _ in results} == {"payload-1"}
    # The per-key lock is released and dropped once nobody waits on it.
    assert cache._locks == {}


@pytest.mark.asyncio
async def test_failed_build_caches_nothing() -> None:
    cache: SingleFlightCache[str, str] = SingleFlightCache(clock=_Clock())
    attempts = 0

    async def _failing() -> str:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("down")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cache.get("k", _failing, ttl_seconds=60)

    assert attempts == 2
    assert cache.peek("k") is None


@pytest.mark.asyncio
async def test_invalidate_where_clear_and_ttl_floor() -> None:
    clock = _Clock()
    cache: SingleFlightCache[tuple[str, str], str] = SingleFlightCache(clock=clock)
    builders = {key: _Builder(key[0] + key[1]) for key in [("c1", "g"), ("c1", "s"), ("c2", "g")]}
    for key, builder in builders.items():
        await cache.get(key, builder, ttl_seconds=0)
    assert len(cache) == 3

    # A zero TTL is floored to one second, so the entries are still live now.
    assert cache.peek(("c2", "g")) == "c2g-1"
    cache.invalidate_where(lambda key: key[0] == "c1")
    assert cache.peek(("c1", "g")) is None
    assert cache.peek(("c1", "s")) is None
    assert cache.peek(("c2", "g")) == "c2g-1"

    cache.invalidate(("c2", "g"))
    assert len(cache) == 0

    await cache.get(("c2", "g"), builders[("c2", "g")], ttl_seconds=60)
    clock.now += 61
    # An expired entry is swept by invalidate_where even when the predicate rejects it.
    cache.invalidate_where(lambda key: False)
    assert len(cache) == 0

    await cache.get(("c2", "g"), builders[("c2", "g")], ttl_seconds=60)
    cache.clear()
    assert len(cache) == 0
