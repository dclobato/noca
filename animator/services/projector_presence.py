#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Best-effort count of the projectors watching one ceremony scope.

The operator drives a ceremony from a phone on stage and cannot see the hall's
projectors. The only signal the server has that a projector exists is its open
``/reveal/events`` stream, so every such stream registers itself here for as
long as it is open, and the controller-lease responses carry the count back to
whichever panel holds the lease.

Presence is one sorted set per ``(contest, scope)`` keyed by a random
per-connection id and scored by the entry's **Valkey-side** expiry instant,
renewed at a third of the TTL while the stream lives. Scoring with the server's
own ``TIME`` rather than each replica's clock keeps the count honest across
replicas, and expiring by score means a replica that dies with streams open
leaks nothing for longer than one TTL.

Everything here is best-effort. A failed registration or renewal is logged and
otherwise ignored -- the stream itself must never depend on it -- and a count
that cannot be read is ``None``, which the operator sees as *unknown*, never as
zero: an empty hall and an unreachable Valkey are different facts.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import AsyncIterator
from typing import Protocol, cast

from shared.services.valkey_service.revelation import reveal_projectors_key

logger = logging.getLogger(__name__)

__all__ = ["ProjectorPresence", "ProjectorPresenceClient"]

_ATTEND_SCRIPT = """
local now = tonumber(redis.call('TIME')[1])
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return 1
"""
"""Register or renew one projector: ``ARGV[1]`` id, ``ARGV[2]`` TTL seconds.

The key's own ``EXPIRE`` is the safety net for a scope whose last projector
vanished without a ``ZREM``: once no entry is renewed, the whole set goes.
"""

_LEAVE_SCRIPT = """
return redis.call('ZREM', KEYS[1], ARGV[1])
"""

_COUNT_SCRIPT = """
local now = tonumber(redis.call('TIME')[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
return redis.call('ZCARD', KEYS[1])
"""
"""Drop every entry whose expiry has passed, then count what is left."""


class ProjectorPresenceClient(Protocol):
    """The scripting surface presence needs; ``ValkeyRuntime`` satisfies it."""

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        """Run a script, answering ``None`` when Valkey is unavailable."""


class ProjectorPresence:
    """Register open ceremony streams and count them per scope."""

    def __init__(self, client: ProjectorPresenceClient, *, ttl_seconds: int) -> None:
        """Bind the gauge to a Valkey client and an entry lifetime.

        Args:
            client: Scripting client; a ``None`` result means unavailable.
            ttl_seconds: Lifetime of one entry; renewed at a third of it.
        """
        self._client = client
        self._ttl = max(1, ttl_seconds)
        self._departures: set[asyncio.Task[None]] = set()

    @property
    def ttl_seconds(self) -> int:
        """Lifetime of one presence entry."""
        return self._ttl

    async def _attend_once(self, key: str, member: str) -> None:
        try:
            if await self._client.eval(_ATTEND_SCRIPT, 1, key, member, str(self._ttl)) is None:
                logger.warning("projector presence for %s could not be recorded", key)
        except Exception as exc:  # pragma: no cover - defensive: never fail the stream
            logger.warning("projector presence for %s failed: %s", key, exc)

    async def _renew_forever(self, key: str, member: str) -> None:
        interval = max(1.0, self._ttl / 3)
        while True:
            await asyncio.sleep(interval)
            await self._attend_once(key, member)

    @contextlib.asynccontextmanager
    async def attend(self, contest_id: str, scope: str) -> AsyncIterator[None]:
        """Count one projector in ``scope`` for the duration of the block.

        Registration happens before the body runs and removal after it ends,
        both best-effort; a renewal task keeps the entry alive in between. The
        body is never refused: this is a gauge, not a gate.

        Removal runs in a task of its own rather than being awaited here. The
        block ends because the client disconnected, and that teardown runs
        inside the response's cancel scope, where every ``await`` is cancelled
        again -- an awaited ``ZREM`` would simply never reach Valkey and the
        entry would linger for one TTL on every clean disconnect.

        Args:
            contest_id: Contest the ceremony belongs to.
            scope: Canonical ceremony scope (a site id, or ``"global"``).
        """
        key = reveal_projectors_key(contest_id, scope)
        member = secrets.token_urlsafe(12)
        await self._attend_once(key, member)
        renewer = asyncio.create_task(self._renew_forever(key, member))
        try:
            yield
        finally:
            renewer.cancel()
            departure = asyncio.create_task(self._leave(key, member))
            self._departures.add(departure)
            departure.add_done_callback(self._departures.discard)

    async def _leave(self, key: str, member: str) -> None:
        try:
            await self._client.eval(_LEAVE_SCRIPT, 1, key, member)
        except Exception as exc:  # pragma: no cover - defensive: the entry expires anyway
            logger.warning("projector presence removal for %s failed (it expires on its own): %s", key, exc)

    async def count(self, contest_id: str, scope: str) -> int | None:
        """Return how many projectors currently watch ``scope``.

        Returns:
            The number of unexpired entries, or ``None`` when Valkey could not
            answer -- deliberately not ``0``, so an outage never reads as an
            empty hall.
        """
        key = reveal_projectors_key(contest_id, scope)
        try:
            result = await self._client.eval(_COUNT_SCRIPT, 1, key)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("projector presence count for %s failed: %s", key, exc)
            return None
        if result is None:
            return None
        return int(cast(int, result))
