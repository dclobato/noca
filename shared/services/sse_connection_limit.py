#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Per-IP and per-user caps on *concurrent* SSE connections.

The request limiter (:mod:`shared.services.request_rate_limit`) counts requests
inside a fixed window; an SSE stream is one request that lives for minutes or
hours, so the resource to bound is the number of streams **held open** at once.
This module is a Valkey-backed connection *lease*:

- ``noca:sse:{bucket}:ip:{address}`` and ``noca:sse:{bucket}:user:{id}`` are
  gauges, incremented atomically on connect and decremented on disconnect.
- Every key carries a TTL that a background task renews while the connection
  is open, so a legitimately long stream never loses its lease, while a process
  that dies mid-stream leaks a slot for at most one TTL.
- A release never leaves a negative or persistent counter (``DECR`` then ``DEL``
  at zero), so releasing after an expiry, or a key that never existed, is harmless.
- **Fail-open**: when Valkey cannot answer, the connection is admitted and the
  outage logged. Nothing is refused *because* Valkey is down -- the animator's
  process ceiling is the Valkey-independent floor, and the other streams need
  Valkey to carry anything at all.

The client IP is always ``request.client.host`` -- proxy-corrected -- and never a
forwarded header. Modules wrap :func:`sse_connection_slots` in a FastAPI yield
dependency: its teardown runs only after the streamed response finishes, which
for SSE is precisely "the client disconnected", and the ``429`` is an ordinary
pre-handler response.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import HTTPException, Request
from valkey.exceptions import ValkeyError

from shared.services.request_rate_limit import (
    TrustedNetworks,
    ValkeyGetter,
    ValkeyLimiterClient,
    default_valkey_getter,
    get_client_ip,
    is_trusted_ip,
)

__all__ = [
    "ACQUIRE_SCRIPT",
    "KEY_PREFIX",
    "RELEASE_SCRIPT",
    "RENEW_SCRIPT",
    "SseSlotPolicy",
    "sse_connection_slots",
]

logger = logging.getLogger(__name__)

KEY_PREFIX = "noca:sse"

RETRY_AFTER_SECONDS = 5
"""Advisory ``Retry-After`` on a refused stream: a slot frees when a peer disconnects."""

ACQUIRE_SCRIPT = """
local count = redis.call("INCR", KEYS[1])
if count > tonumber(ARGV[1]) then
  redis.call("DECR", KEYS[1])
  return 0
end
redis.call("EXPIRE", KEYS[1], ARGV[2])
return count
"""

RELEASE_SCRIPT = """
local count = redis.call("DECR", KEYS[1])
if count <= 0 then
  redis.call("DEL", KEYS[1])
end
return count
"""

RENEW_SCRIPT = """
if redis.call("EXISTS", KEYS[1]) == 1 then
  redis.call("EXPIRE", KEYS[1], ARGV[1])
  return 1
end
return 0
"""

_RECOVERABLE = (ValkeyError, ConnectionError, TimeoutError, OSError)


@dataclass(slots=True, frozen=True)
class SseSlotPolicy:
    """Concurrent-connection policy for one module's SSE streams.

    Attributes:
        bucket: Namespace isolating this module's gauges (for example ``"web:sse"``).
        max_per_ip: Streams one client IP may hold open at once.
        max_per_user: Streams one authenticated user may hold open at once, across
            IPs. Ignored when the caller supplies no user id.
        ttl_seconds: Lease lifetime; renewed at a third of it while connected.
        trusted_networks: Networks whose clients bypass the caps entirely.
        enabled: When ``False`` the dependency is a no-op.
    """

    bucket: str
    max_per_ip: int
    max_per_user: int
    ttl_seconds: int
    trusted_networks: TrustedNetworks = ()
    enabled: bool = True


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | str | bytes | bytearray):
        return None
    try:
        return int(value)
    except ValueError:
        return None


async def _acquire(client: ValkeyLimiterClient, *, key: str, limit: int, ttl: int) -> bool | None:
    """Take one slot on ``key``. ``None`` means Valkey could not answer (fail open)."""
    try:
        result = await client.eval(ACQUIRE_SCRIPT, 1, key, str(limit), str(ttl))
    except _RECOVERABLE as exc:
        logger.warning("SSE slot gauge unavailable for %s, admitting the stream: %s", key, exc)
        return None
    count = _coerce_int(result)
    if count is None:
        return None
    return count > 0


async def _release(client: ValkeyLimiterClient, key: str) -> None:
    """Give one slot on ``key`` back; never raises."""
    try:
        await client.eval(RELEASE_SCRIPT, 1, key)
    except _RECOVERABLE as exc:
        logger.warning("SSE slot release failed for %s (the lease expires on its own): %s", key, exc)


async def _renew_forever(client: ValkeyLimiterClient, keys: tuple[str, ...], ttl: int) -> None:
    """Re-``EXPIRE`` every held key at a third of the TTL until cancelled."""
    interval = max(1.0, ttl / 3)
    while True:
        await asyncio.sleep(interval)
        for key in keys:
            try:
                await client.eval(RENEW_SCRIPT, 1, key, str(ttl))
            except _RECOVERABLE as exc:
                logger.warning("SSE slot renewal failed for %s: %s", key, exc)


@contextlib.asynccontextmanager
async def sse_connection_slots(
    request: Request,
    *,
    policy: SseSlotPolicy,
    user_id: str | None = None,
    valkey_getter: ValkeyGetter | None = None,
    detail: str = "Too many open event streams.",
) -> AsyncIterator[None]:
    """Hold an IP slot -- and a user slot when ``user_id`` is given -- for one stream.

    Args:
        request: Current HTTP request (its ``client.host`` keys the IP slot).
        policy: Bucket, ceilings, TTL, and trusted networks.
        user_id: Stable id of the authenticated user, or ``None`` for an
            anonymous stream.
        valkey_getter: Resolves the Valkey client; defaults to
            ``request.app.state.valkey_runtime``.
        detail: Error detail of the ``429`` response.

    Raises:
        HTTPException: ``429`` with ``Retry-After`` when either slot is exhausted.
    """
    client_ip = get_client_ip(request) or "unknown"
    client = (valkey_getter or default_valkey_getter)(request)
    if not policy.enabled or client is None or is_trusted_ip(client_ip, policy.trusted_networks):
        yield
        return

    ttl = max(1, policy.ttl_seconds)
    wanted: list[tuple[str, int]] = [(f"{KEY_PREFIX}:{policy.bucket}:ip:{client_ip}", max(1, policy.max_per_ip))]
    if user_id:
        wanted.append((f"{KEY_PREFIX}:{policy.bucket}:user:{user_id}", max(1, policy.max_per_user)))

    held: list[str] = []
    try:
        for key, limit in wanted:
            admitted = await _acquire(client, key=key, limit=limit, ttl=ttl)
            if admitted is None:
                continue  # fail open: this gauge is not tracked for the stream
            if not admitted:
                raise HTTPException(status_code=429, detail=detail, headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
            held.append(key)
        renewal = asyncio.create_task(_renew_forever(client, tuple(held), ttl)) if held else None
        try:
            yield
        finally:
            if renewal is not None:
                renewal.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await renewal
    finally:
        for key in held:
            await _release(client, key)
