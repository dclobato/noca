#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Process-local fallback limiter for authentication throttling.

This is the degraded-mode backend used by :mod:`shared.services.auth_rate_limit`
when Valkey is unavailable or errors out. It keeps counters in memory so a login
flow keeps some protection without ever failing a request because the shared
store is down.

Every limiter registers itself in a process-wide weak registry so an
administrative unlock (:mod:`shared.services.auth_lockout_admin`) can clear
the matching fallback entries of *every* limiter in this process without each
module handing its private instance around. The registry is weak, so a
short-lived limiter built by a test disappears with it.
"""

from __future__ import annotations

import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field

__all__ = [
    "InMemoryAuthRateLimiter",
    "fallback_lock_ttls_matching",
    "reset_all_fallback_limiters_matching",
]


@dataclass(slots=True)
class _FallbackBucket:
    """Process-local counter or lock entry."""

    expires_at: float
    count: int = 0


@dataclass(slots=True)
class _FallbackSet:
    """Process-local bounded set entry with an expiry."""

    expires_at: float
    members: set[str] = field(default_factory=set)


@dataclass(slots=True, eq=False, weakref_slot=True)
class InMemoryAuthRateLimiter:
    """Fallback limiter used when Valkey is unavailable.

    ``eq=False`` keeps identity hashing, which the weak registry needs.
    """

    _buckets: dict[str, _FallbackBucket] = field(default_factory=dict)
    _sets: dict[str, _FallbackSet] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Register this limiter so administrative unlocks can reach it."""
        _REGISTRY.add(self)

    def active_ttl(self, key: str, *, now: float | None = None) -> int | None:
        """Return seconds until lock expiry, or ``None`` when no lock exists."""
        current = time.monotonic() if now is None else now
        bucket = self._buckets.get(key)
        if bucket is None:
            return None
        if bucket.expires_at <= current:
            self._buckets.pop(key, None)
            return None
        return max(1, int(bucket.expires_at - current))

    def record_failure(
        self,
        failure_key: str,
        lock_key: str,
        *,
        window_seconds: int,
        max_failures: int,
        lockout_seconds: int,
        now: float | None = None,
        distinct_key: str | None = None,
        distinct_member: str | None = None,
        distinct_required: int = 0,
        distinct_max_members: int = 0,
    ) -> int | None:
        """Increment a bucket and return lock TTL when the threshold is reached.

        Args:
            failure_key: Counter key for this bucket.
            lock_key: Key set when the bucket locks.
            window_seconds: Failure-count window.
            max_failures: Failures tolerated inside the window.
            lockout_seconds: Lock duration once the bucket trips.
            now: Optional monotonic timestamp override for tests.
            distinct_key: Set key holding recently-failed identifier hashes.
            distinct_member: Identifier hash of this attempt, when known.
            distinct_required: Distinct identifiers that must also have failed
                before the lock may be applied. ``0`` disables the gate.
            distinct_max_members: Ceiling on the tracked set's size.

        Returns:
            The lock TTL in seconds when this failure created a lock, else ``None``.
        """
        current = time.monotonic() if now is None else now
        bucket = self._buckets.get(failure_key)
        if bucket is None or bucket.expires_at <= current:
            bucket = _FallbackBucket(expires_at=current + window_seconds)
            self._buckets[failure_key] = bucket
        bucket.count += 1
        distinct = self._record_distinct(
            distinct_key,
            distinct_member,
            window_seconds=window_seconds,
            max_members=distinct_max_members,
            now=current,
        )
        if bucket.count < max_failures:
            return None
        if distinct_required > 0 and distinct < distinct_required:
            return None
        self._buckets[lock_key] = _FallbackBucket(expires_at=current + lockout_seconds, count=1)
        return lockout_seconds

    def reset(self, keys: list[str]) -> None:
        """Delete local counters, locks, or distinct-identifier sets."""
        for key in keys:
            self._buckets.pop(key, None)
            self._sets.pop(key, None)

    def _record_distinct(
        self,
        distinct_key: str | None,
        member: str | None,
        *,
        window_seconds: int,
        max_members: int,
        now: float,
    ) -> int:
        """Add *member* to the bounded set at *distinct_key* and return its size."""
        if distinct_key is None:
            return 0
        entry = self._sets.get(distinct_key)
        if entry is None or entry.expires_at <= now:
            entry = _FallbackSet(expires_at=now + window_seconds)
            self._sets[distinct_key] = entry
        entry.expires_at = now + window_seconds
        if member is not None and (max_members <= 0 or len(entry.members) < max_members):
            entry.members.add(member)
        return len(entry.members)

    def reset_matching(self, predicate: Callable[[str], bool]) -> int:
        """Delete every entry whose key satisfies ``predicate`` and return how many.

        Both stores are swept, because a key is a counter/lock *or* a
        distinct-identifier set and never both: whichever kind the predicate
        admits, it must be cleared here as well as in Valkey, or the same
        unlock would leave different state behind in the two backends.
        """
        matching = [key for key in (*self._buckets, *self._sets) if predicate(key)]
        for key in matching:
            self._buckets.pop(key, None)
            self._sets.pop(key, None)
        return len(matching)

    def lock_ttls_matching(self, predicate: Callable[[str], bool]) -> dict[str, int]:
        """Return ``{key: seconds_left}`` for every live entry whose key satisfies ``predicate``."""
        ttls: dict[str, int] = {}
        for key in list(self._buckets):
            if not predicate(key):
                continue
            ttl = self.active_ttl(key)
            if ttl is not None:
                ttls[key] = ttl
        return ttls


_REGISTRY: weakref.WeakSet[InMemoryAuthRateLimiter] = weakref.WeakSet()


def reset_all_fallback_limiters_matching(predicate: Callable[[str], bool]) -> int:
    """Clear matching entries in every limiter of this process and return the total removed."""
    return sum(limiter.reset_matching(predicate) for limiter in list(_REGISTRY))


def fallback_lock_ttls_matching(predicate: Callable[[str], bool]) -> dict[str, int]:
    """Merge the live matching entries of every limiter, keeping the longest TTL per key."""
    merged: dict[str, int] = {}
    for limiter in list(_REGISTRY):
        for key, ttl in limiter.lock_ttls_matching(predicate).items():
            merged[key] = max(ttl, merged.get(key, 0))
    return merged
