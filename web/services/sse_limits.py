#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Concurrent-SSE-connection caps for the Web event streams.

Both Web streams share one bucket, ``web:sse``, so a client's open
``/c/{slug}/live/events`` and ``/c/{slug}/runs/events`` connections count
against the same ``NOCA_WEB_SSE_MAX_PER_IP`` ceiling. The authenticated runs
stream additionally holds a per-actor slot (``NOCA_WEB_SSE_MAX_PER_USER``),
keyed on the stable ``User`` / ``UberAdmin`` id, so one account cannot spread
its streams across addresses.

Each dependency is a *yield* dependency: FastAPI runs its teardown only after
the streamed response finishes, which for SSE is "the client disconnected", and
that teardown is what releases the slots. The policy is rebuilt from
``settings`` on every call so tests can monkeypatch the knobs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Request

from shared.services.request_rate_limit import parse_trusted_cidrs
from shared.services.sse_connection_limit import SseSlotPolicy, sse_connection_slots
from web.config import settings
from web.dependencies import ContestContext, get_contest_context

__all__ = ["SSE_LIMIT_BUCKET", "SSE_LIMIT_DETAIL", "enforce_live_events_slots", "enforce_runs_events_slots"]

SSE_LIMIT_BUCKET = "web:sse"
SSE_LIMIT_DETAIL = "Too many open contest event streams."


def sse_slot_policy() -> SseSlotPolicy:
    """Build the Web SSE connection-cap policy from the current settings."""
    return SseSlotPolicy(
        bucket=SSE_LIMIT_BUCKET,
        max_per_ip=settings.SSE_MAX_PER_IP,
        max_per_user=settings.SSE_MAX_PER_USER,
        ttl_seconds=settings.SSE_CONNECTION_TTL_SECONDS,
        trusted_networks=parse_trusted_cidrs(settings.SSE_TRUSTED_CIDRS),
        enabled=settings.SSE_LIMIT_ENABLED,
    )


async def enforce_live_events_slots(request: Request) -> AsyncIterator[None]:
    """Hold a per-IP slot for the anonymous live feed stream until it ends."""
    async with sse_connection_slots(request, policy=sse_slot_policy(), detail=SSE_LIMIT_DETAIL):
        yield


async def enforce_runs_events_slots(
    request: Request, ctx: ContestContext = Depends(get_contest_context)
) -> AsyncIterator[None]:
    """Hold a per-IP and a per-actor slot for the runs stream until it ends.

    ``ctx`` is the same dependency the route declares; FastAPI resolves it once
    per request, so this adds no second authentication or database round trip.
    """
    async with sse_connection_slots(
        request, policy=sse_slot_policy(), user_id=str(ctx.actor.id), detail=SSE_LIMIT_DETAIL
    ):
        yield
