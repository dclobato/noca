#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Process-local short-circuit for the mandatory-announcement pop-up.

``load_pending_required_announcement`` runs on every authenticated HTML page
load, and on almost every day in almost every deployment its answer is the
same for everyone: there is no required Arena announcement at all. That
user-independent half of the question is what this module caches -- one
boolean per process, for :data:`REQUIRED_ANNOUNCEMENTS_CACHE_SECONDS` -- so
the common page view costs a dictionary lookup rather than a pooled connection
and a query. The per-user ledger check still runs, unchanged, whenever a
required announcement exists; it is one index probe per required announcement
and needs no cache of its own.

The cache is deliberately process-local (the :class:`SingleFlightCache`
contract): the admin publish and delete routes invalidate their own process
right after commit, and the TTL bounds how late a fresh mandatory notice can
reach users served by another replica. A build that fails caches nothing, so an
outage is never pinned for a whole TTL.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.announcement_acknowledgment_service import has_required_announcements
from shared.services.single_flight_cache import SingleFlightCache

#: How long a process trusts its last answer to "does any required Arena
#: announcement exist". A required announcement published on another replica is
#: shown to this replica's users at most this many seconds late.
REQUIRED_ANNOUNCEMENTS_CACHE_SECONDS = 30

_KEY = "arena"

#: Injectable for tests; resolved on every read so a patched clock takes effect.
_clock: Callable[[], float] = time.monotonic
_cache: SingleFlightCache[str, bool] = SingleFlightCache(clock=lambda: _clock())


def required_announcements_known_absent() -> bool:
    """Return whether this process holds a live "none exist" answer.

    Reads the cache without building, so the caller can decide to skip opening a
    session at all. A missing or expired entry answers ``False``: unknown is not
    the same as absent.
    """
    return _cache.peek(_KEY) is False


async def required_announcements_exist(session: AsyncSession) -> bool:
    """Return whether any required Arena announcement exists, building the cache on a miss.

    Concurrent misses in one process share a single query.

    Args:
        session: Async SQLAlchemy session used only when the cache has to be rebuilt.

    Returns:
        bool: ``True`` when a required Arena announcement is on file.
    """

    async def _build() -> bool:
        return await has_required_announcements(session)

    value, _ = await _cache.get(_KEY, _build, ttl_seconds=REQUIRED_ANNOUNCEMENTS_CACHE_SECONDS)
    return value


def invalidate_required_announcements_cache() -> None:
    """Drop the cached answer so the next page load asks the database again.

    Called by the admin publish and delete routes **after** their commit: an
    invalidation before it could be rebuilt from the pre-commit state by a
    concurrent page load and pin the stale answer for another TTL.
    """
    _cache.invalidate(_KEY)
