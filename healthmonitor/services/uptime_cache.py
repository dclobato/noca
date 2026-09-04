#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Per-process cache for the ``/uptime.json`` payload.

The uptime history only changes when the prober records a pass, once per
``NOCA_HEALTHMON_PROBE_INTERVAL``, yet every open dashboard tab re-fetches it
every 30 seconds. This cache keeps the last built payload in memory for one
probe interval and collapses concurrent misses into a single build, so an
anonymous flood costs Valkey at most one pipelined read per interval per
replica. The prober invalidates it after every recording pass so a fresh probe
is visible on the next request rather than up to one interval later.

It is a single-key view over the shared
:class:`shared.services.single_flight_cache.SingleFlightCache`, which owns the
single-flight and TTL mechanics; this module keeps the one-value API the
dashboard route and the prober loop use.
"""

import time
from collections.abc import Awaitable, Callable

from shared.services.single_flight_cache import SingleFlightCache

_KEY = "uptime"


class UptimeHistoryCache[T]:
    """Single-flight TTL cache holding one value.

    Args:
        ttl_seconds: Seconds a built value stays valid.
        clock: Monotonic clock, injectable for tests.
    """

    def __init__(self, *, ttl_seconds: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl_seconds = max(1, ttl_seconds)
        self._cache: SingleFlightCache[str, T] = SingleFlightCache(clock=clock)

    @property
    def ttl_seconds(self) -> int:
        """Configured lifetime of a built value in seconds."""
        return self._ttl_seconds

    async def get(self, build: Callable[[], Awaitable[T]]) -> tuple[T, int]:
        """Return the cached value, building it once when missing or expired.

        Args:
            build: Coroutine factory producing a fresh value.

        Returns:
            The value and the whole seconds until it expires (at least ``1``),
            suitable for a ``Cache-Control: max-age`` directive.
        """
        return await self._cache.get(_KEY, build, ttl_seconds=self._ttl_seconds)

    def invalidate(self) -> None:
        """Drop the cached value so the next request rebuilds it."""
        self._cache.invalidate(_KEY)
