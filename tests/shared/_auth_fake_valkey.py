#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""A fake Valkey ``eval``/``delete``/``scan`` that interprets the auth-throttle scripts.

``shared.services.auth_rate_limit`` runs two Lua scripts (record a failure and
set the lock at the threshold; read a lock's TTL) and deletes keys on reset.
This fake keeps counters and locks in memory against a **controllable clock**,
so a test exercises the real key names, thresholds, window, and lockout
duration -- and can prove a lock lifts after ``lockout_seconds`` by advancing
the clock -- rather than a stubbed verdict. Underscore-prefixed so pytest does
not collect it.

It also interprets the shared fixed-window counter
(:data:`shared.services.request_rate_limit.RATE_LIMIT_SCRIPT`), because a page
under test may sit behind one of those ceilings as well as behind the auth
throttle -- the Arena admin user profile carries both. Interpreting it on the
same clock keeps that incidental limiter honest instead of stubbing it: a test
that loops a route enough times still trips it, exactly as production would.
"""

from __future__ import annotations

from collections.abc import Sequence
from fnmatch import fnmatchcase

from shared.services.auth_rate_limit import _FAIL_DISTINCT_SCRIPT, _FAIL_SCRIPT, _TTL_SCRIPT
from shared.services.request_rate_limit import RATE_LIMIT_SCRIPT

__all__ = ["AUTH_SCRIPTS", "AuthFakeValkey"]

AUTH_SCRIPTS = frozenset({_FAIL_SCRIPT, _FAIL_DISTINCT_SCRIPT, _TTL_SCRIPT, RATE_LIMIT_SCRIPT})


class AuthFakeValkey:
    """In-memory failure counters and lock TTLs for the auth throttle."""

    def __init__(self) -> None:
        self.clock = 0.0
        self.counts: dict[str, tuple[int, float]] = {}  # key -> (count, expires_at)
        self.locks: dict[str, float] = {}  # key -> expires_at
        self.sets: dict[str, tuple[set[str], float]] = {}  # key -> (members, expires_at)
        self.unavailable = False
        """When set, the administrative scan/delete pair answers ``None`` like a down store."""

    def advance(self, seconds: float) -> None:
        """Move the fake clock forward, expiring windows and locks on the way."""
        self.clock += seconds
        self.counts = {k: v for k, v in self.counts.items() if v[1] > self.clock}
        self.locks = {k: v for k, v in self.locks.items() if v > self.clock}
        self.sets = {k: v for k, v in self.sets.items() if v[1] > self.clock}

    def _lock_ttl(self, key: str) -> int:
        expires_at = self.locks.get(key)
        if expires_at is None or expires_at <= self.clock:
            self.locks.pop(key, None)
            return -2
        return max(1, int(expires_at - self.clock))

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        if script == _FAIL_SCRIPT:
            failure_key, lock_key, window, max_failures, lockout = args
            count, expires_at = self.counts.get(failure_key, (0, 0.0))
            if expires_at <= self.clock:
                count, expires_at = 0, self.clock + int(window)
            count += 1
            self.counts[failure_key] = (count, expires_at)
            if count >= int(max_failures):
                self.locks[lock_key] = self.clock + int(lockout)
            return [count, self._lock_ttl(lock_key)]
        if script == _FAIL_DISTINCT_SCRIPT:
            failure_key, lock_key, accounts_key = args[0], args[1], args[2]
            window, max_failures, lockout = int(args[3]), int(args[4]), int(args[5])
            member, max_members, required = args[6], int(args[7]), int(args[8])
            count, expires_at = self.counts.get(failure_key, (0, 0.0))
            if expires_at <= self.clock:
                count, expires_at = 0, self.clock + window
            count += 1
            self.counts[failure_key] = (count, expires_at)
            members, _ = self.sets.get(accounts_key, (set(), 0.0))
            if member and len(members) < max_members:
                members = members | {member}
            self.sets[accounts_key] = (members, self.clock + window)
            if count >= max_failures and len(members) >= required:
                self.locks[lock_key] = self.clock + lockout
            return [count, self._lock_ttl(lock_key)]
        if script == _TTL_SCRIPT:
            return self._lock_ttl(args[0])
        if script == RATE_LIMIT_SCRIPT:
            # Fixed window: INCR, expire on first hit, answer (count, ttl_ms).
            key, window = args[0], int(args[1])
            count, expires_at = self.counts.get(key, (0, 0.0))
            if expires_at <= self.clock:
                count, expires_at = 0, self.clock + window
            count += 1
            self.counts[key] = (count, expires_at)
            return [count, int(max(0.0, expires_at - self.clock) * 1000)]
        raise AssertionError(f"unexpected script: {script!r}")

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += (
                int(self.counts.pop(key, None) is not None)
                + int(self.locks.pop(key, None) is not None)
                + int(self.sets.pop(key, None) is not None)
            )
        return removed

    def live_keys(self) -> list[str]:
        """Every key that has not expired on the fake clock."""
        counters = [key for key, (_, expires_at) in self.counts.items() if expires_at > self.clock]
        locks = [key for key, expires_at in self.locks.items() if expires_at > self.clock]
        sets = [key for key, (_, expires_at) in self.sets.items() if expires_at > self.clock]
        return sorted(set(counters) | set(locks) | set(sets))

    async def scan_keys(self, pattern: str) -> list[str] | None:
        """Mirror ``ValkeyRuntime.scan_keys`` over the live keys."""
        if self.unavailable:
            return None
        return [key for key in self.live_keys() if fnmatchcase(key, pattern)]

    async def delete_keys_counted(self, keys: Sequence[str]) -> int | None:
        """Mirror ``ValkeyRuntime.delete_keys_counted``: ``None`` when down, else a count."""
        if self.unavailable:
            return None
        live = set(self.live_keys())
        removed = 0
        for key in keys:
            if key in live:
                removed += await self.delete(key)
        return removed
