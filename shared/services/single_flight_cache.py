#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Process-local, keyed, single-flight TTL cache.

A small building block for read paths that are anonymous, expensive, and
re-requested far more often than their inputs change: the health monitor's
uptime payload, the animator's scoreboard snapshot, and the reveal ceremony's
frozen dataset. Each key holds one value for a caller-chosen TTL, and concurrent
misses on the same key wait on one per-key lock and share a single build, so a
flood of identical requests costs the backing store one build per TTL per
process.

The cache is deliberately process-local: a multi-replica deployment builds once
per replica, which is still bounded and needs no shared state. Invalidation is
explicit (``invalidate``, ``invalidate_where``, ``clear``) so a producer that
knows the inputs changed can drop entries ahead of their TTL.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class _Entry[T]:
    """One cached value and the monotonic instant at which it expires."""

    value: T
    expires_at: float


class SingleFlightCache[K: Hashable, T]:
    """Keyed single-flight TTL cache.

    Args:
        clock: Monotonic clock, injectable for tests.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[K, _Entry[T]] = {}
        self._locks: dict[K, asyncio.Lock] = {}

    def _current(self, key: K) -> _Entry[T] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            # Evict on read so the map stays bounded by live keys.
            self._entries.pop(key, None)
            return None
        return entry

    def _remaining(self, entry: _Entry[T]) -> int:
        return max(1, math.ceil(entry.expires_at - self._clock()))

    async def get(self, key: K, build: Callable[[], Awaitable[T]], *, ttl_seconds: int) -> tuple[T, int]:
        """Return the value for ``key``, building it once when missing or expired.

        Concurrent callers that all miss the same key wait on one lock and share
        the first caller's build. A build that raises caches nothing and
        re-raises, so an outage is never pinned for a whole TTL.

        Args:
            key: Cache key.
            build: Coroutine factory producing a fresh value.
            ttl_seconds: Seconds the built value stays valid (floored at ``1``).

        Returns:
            The value and the whole seconds until it expires (at least ``1``),
            suitable for a ``Cache-Control: max-age`` directive.
        """
        entry = self._current(key)
        if entry is not None:
            return entry.value, self._remaining(entry)
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                entry = self._current(key)
                if entry is None:
                    value = await build()
                    entry = _Entry(value=value, expires_at=self._clock() + max(1, ttl_seconds))
                    self._entries[key] = entry
                return entry.value, self._remaining(entry)
        finally:
            # Drop the lock once nobody is waiting on it, so the lock map stays
            # bounded by keys currently being built rather than ever seen.
            if not lock.locked() and self._locks.get(key) is lock:
                self._locks.pop(key, None)

    def peek(self, key: K) -> T | None:
        """Return the live cached value for ``key`` without building, or ``None``."""
        entry = self._current(key)
        return None if entry is None else entry.value

    def invalidate(self, key: K) -> None:
        """Drop one key so its next request rebuilds."""
        self._entries.pop(key, None)

    def invalidate_where(self, predicate: Callable[[K], bool]) -> None:
        """Drop every key ``predicate`` accepts, sweeping expired entries too."""
        now = self._clock()
        for key in list(self._entries):
            entry = self._entries[key]
            if entry.expires_at <= now or predicate(key):
                self._entries.pop(key, None)

    def clear(self) -> None:
        """Drop every cached value."""
        self._entries.clear()

    def __len__(self) -> int:
        """Number of stored entries, expired ones included until swept."""
        return len(self._entries)
