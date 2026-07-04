#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Process-local fallback limiter for authentication throttling.

This is the degraded-mode backend used by :mod:`shared.services.auth_rate_limit`
when Valkey is unavailable or errors out. It keeps counters in memory so a login
flow keeps some protection without ever failing a request because the shared
store is down.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class _FallbackBucket:
    """Process-local counter or lock entry."""

    expires_at: float
    count: int = 0


@dataclass(slots=True)
class InMemoryAuthRateLimiter:
    """Fallback limiter used when Valkey is unavailable."""

    _buckets: dict[str, _FallbackBucket] = field(default_factory=dict)

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
    ) -> int | None:
        """Increment a bucket and return lock TTL when the threshold is reached."""
        current = time.monotonic() if now is None else now
        bucket = self._buckets.get(failure_key)
        if bucket is None or bucket.expires_at <= current:
            bucket = _FallbackBucket(expires_at=current + window_seconds)
            self._buckets[failure_key] = bucket
        bucket.count += 1
        if bucket.count < max_failures:
            return None
        self._buckets[lock_key] = _FallbackBucket(expires_at=current + lockout_seconds, count=1)
        return lockout_seconds

    def reset(self, keys: list[str]) -> None:
        """Delete local counters or locks."""
        for key in keys:
            self._buckets.pop(key, None)
