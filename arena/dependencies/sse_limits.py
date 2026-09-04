#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Concurrent-SSE-connection caps for the Arena event streams.

Both Arena streams -- ``/live/events`` and ``/user/submissions/status/events`` --
share one bucket, ``arena:sse``: a client's open streams on either route count
against the same ``NOCA_ARENA_SSE_MAX_PER_IP`` ceiling, and the authenticated
user behind them holds a per-user slot (``NOCA_ARENA_SSE_MAX_PER_USER``) keyed
on the stable ``ArenaUser.id``, so one account cannot spread its streams across
addresses. The user is resolved through the short-lived
:func:`arena.dependencies.auth.get_streaming_arena_user`, which closes its
session before the stream opens; an anonymous request (``None``) simply holds
the IP slot -- the app-wide gate, not this module, decides who may connect.

As a *yield* dependency its teardown runs only after the streamed response
finishes, which for SSE is "the client disconnected", and that teardown is what
releases the slots. The policy is rebuilt from ``settings`` on every call so
tests can monkeypatch the knobs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Request

from arena.config import settings
from arena.dependencies.auth import get_streaming_arena_user
from arena.models.arena_users import ArenaUser
from shared.services.request_rate_limit import parse_trusted_cidrs
from shared.services.sse_connection_limit import SseSlotPolicy, sse_connection_slots

__all__ = ["SSE_LIMIT_BUCKET", "SSE_LIMIT_DETAIL", "enforce_sse_connection_caps"]

SSE_LIMIT_BUCKET = "arena:sse"
SSE_LIMIT_DETAIL = "Too many open Arena event streams."


def sse_slot_policy() -> SseSlotPolicy:
    """Build the Arena SSE connection-cap policy from the current settings."""
    return SseSlotPolicy(
        bucket=SSE_LIMIT_BUCKET,
        max_per_ip=settings.SSE_MAX_PER_IP,
        max_per_user=settings.SSE_MAX_PER_USER,
        ttl_seconds=settings.SSE_CONNECTION_TTL_SECONDS,
        trusted_networks=parse_trusted_cidrs(settings.SSE_TRUSTED_CIDRS),
        enabled=settings.SSE_LIMIT_ENABLED,
    )


async def enforce_sse_connection_caps(
    request: Request, current_user: ArenaUser | None = Depends(get_streaming_arena_user)
) -> AsyncIterator[None]:
    """Hold a per-IP slot, plus a per-user slot when logged in, until the stream ends."""
    user_id = str(current_user.id) if current_user is not None else None
    async with sse_connection_slots(request, policy=sse_slot_policy(), user_id=user_id, detail=SSE_LIMIT_DETAIL):
        yield
