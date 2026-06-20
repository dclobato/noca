#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared Server-Sent Events refresh loop for live-feed pages.

Both the web contest live feed and the Arena live feed expose a lightweight SSE
channel that carries only a generic ``refresh`` signal: no verdict data leaves
the server here, the browser refetches an authoritative JSON snapshot instead.
This module owns the subtle async lifecycle (heartbeat, reconnect, task
cancellation, and generator cleanup) so the two routes cannot drift.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable

_HEARTBEAT_SECONDS = 15.0
_RECONNECT_SECONDS = 1.0


def _always(_event: object) -> bool:
    """Default predicate that emits a refresh for every event."""
    return True


async def iter_refresh_events[EventT](
    *,
    open_event_stream: Callable[[], AsyncGenerator[EventT]],
    is_disconnected: Callable[[], Awaitable[bool]],
    should_emit: Callable[[EventT], bool] = _always,
    emit_initial_ping: bool = False,
    heartbeat_seconds: float = _HEARTBEAT_SECONDS,
    reconnect_seconds: float = _RECONNECT_SECONDS,
) -> AsyncIterator[str]:
    """Yield SSE ``ping``/``refresh`` frames driven by an event stream.

    Emits ``data: refresh\\n\\n`` whenever ``should_emit`` accepts an event from
    ``open_event_stream``, ``data: ping\\n\\n`` on each heartbeat timeout, and
    transparently reopens the stream after it ends while the client stays
    connected. No event payload is forwarded to the client.

    Args:
        open_event_stream: Factory returning a fresh event async generator. It is
            called once per (re)connection so a closed stream can be reopened.
        is_disconnected: Awaitable predicate that is ``True`` once the client
            connection has dropped; the loop stops cooperatively when it returns.
        should_emit: Predicate deciding whether a given event triggers a refresh
            frame. Defaults to emitting for every event.
        emit_initial_ping: When ``True``, yield a single ``ping`` before waiting
            for any event so clients see activity immediately on connect.
        heartbeat_seconds: Idle timeout before emitting a keep-alive ping.
        reconnect_seconds: Delay before reopening the stream after it ends.

    Yields:
        str: SSE frames (``data: ping\\n\\n`` or ``data: refresh\\n\\n``).
    """
    if emit_initial_ping and not await is_disconnected():
        yield "data: ping\n\n"

    while not await is_disconnected():
        event_iter = open_event_stream()
        next_event_task: asyncio.Task[EventT] | None = None
        try:
            while not await is_disconnected():
                if next_event_task is None:
                    next_event_task = asyncio.create_task(anext(event_iter))

                done, _ = await asyncio.wait({next_event_task}, timeout=heartbeat_seconds)
                if not done:
                    yield "data: ping\n\n"
                    continue

                try:
                    event = next_event_task.result()
                except StopAsyncIteration:
                    break
                finally:
                    next_event_task = None

                if should_emit(event):
                    yield "data: refresh\n\n"
        finally:
            if next_event_task is not None:
                next_event_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                    await next_event_task
            with contextlib.suppress(Exception):
                await event_iter.aclose()

        if not await is_disconnected():
            await asyncio.sleep(reconnect_seconds)
