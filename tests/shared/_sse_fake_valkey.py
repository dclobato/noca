#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""A fake Valkey ``eval`` that interprets the SSE lease scripts.

Shared by the shared-service unit tests and every module's stream-cap tests, so
all of them exercise the real key names, limits, and TTL arguments the service
sends rather than a stubbed verdict. The module name is underscore-prefixed so
pytest does not collect it.
"""

from __future__ import annotations

from shared.services.sse_connection_limit import ACQUIRE_SCRIPT, RELEASE_SCRIPT, RENEW_SCRIPT

__all__ = ["SseFakeValkey"]


class SseFakeValkey:
    """In-memory gauges plus a log of every ``EXPIRE`` the scripts issued."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.renewals: list[tuple[str, int]] = []
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        key = args[0]
        self.calls.append((script, args))
        if script == ACQUIRE_SCRIPT:
            limit, ttl = int(args[1]), int(args[2])
            count = self.counts.get(key, 0) + 1
            if count > limit:
                return 0
            self.counts[key] = count
            self.ttls[key] = ttl
            return count
        if script == RELEASE_SCRIPT:
            count = self.counts.get(key, 0) - 1
            if count <= 0:
                self.counts.pop(key, None)
                self.ttls.pop(key, None)
            else:
                self.counts[key] = count
            return count
        if script == RENEW_SCRIPT:
            if key in self.counts:
                self.ttls[key] = int(args[1])
                self.renewals.append((key, int(args[1])))
                return 1
            return 0
        raise AssertionError(f"unexpected script: {script!r}")
