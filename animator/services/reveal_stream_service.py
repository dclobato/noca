#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Spectator ceremony stream: nudges, preceded by a coverage signal.

This module exists for one reason: **the browser's ``open`` event is not the
moment coverage begins.** An SSE response's headers are written when the route
returns its generator, so ``EventSource.onopen`` can fire before the Valkey
``SUBSCRIBE`` behind it has completed. A client that reconciles on ``open``
therefore has a real gap — a mutation published inside it reaches neither the
client's fetch (which already returned) nor its subscription (which does not yet
exist), and because pub/sub has no replay and the operator may not step again,
the projector can stay stale for the rest of the ceremony.

The fix is to make the subscription's completion observable. The shared iterator
signals it through ``on_subscribed``; this module waits for that signal, emits a
``ready`` event, and only then streams nudges. The client refetches on ``ready``
rather than on ``open``, so its reconciliation strictly follows the subscription
and no publication can fall between them.

That ordering requires draining the iterator concurrently: an async generator
does not execute its body — and so never subscribes — until something awaits its
first item. A pump task advances it into a bounded queue, which is also what lets
the ``ready`` event be emitted while the channel is still silent.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing, suppress
from typing import Final, Protocol

from shared.reveal_schema import RevelationEvent

__all__ = [
    "EVENT_REVEAL_MEDIA_CUE",
    "EVENT_REVEAL_READY",
    "EVENT_REVEAL_STATE_CHANGED",
    "RevealEventSource",
    "iter_ready_then_events",
]

logger = logging.getLogger(__name__)

EVENT_REVEAL_READY = "reveal_ready"
"""Emitted once, after the subscription is live: "reconcile now; you are covered"."""

EVENT_REVEAL_STATE_CHANGED = "reveal_state_changed"
"""One ceremony-changed nudge. Metadata only; the client refetches state."""

EVENT_REVEAL_MEDIA_CUE = "reveal_media_cue"
"""One presentation cue: raise or lower a team's media overlay.

Deliberately a *separate* event name rather than a nudge with a flag, because
the client's reaction is the opposite one: a nudge means "refetch authoritative
state", while a cue changes no state and must trigger no fetch at all."""

_QUEUE_MAXSIZE: Final = 64
"""Bound on frames buffered for one slow client.

Neither frame carries state, so dropping one is harmless: a nudge is recovered
by the next nudge or by the client's own reconciliation, and a dropped media cue
costs one press of the operator's button. The pump therefore discards the oldest
entry rather than growing without limit or blocking the subscriber."""

_SUBSCRIBE_TIMEOUT_SECONDS: Final = 10.0
"""How long to wait for the subscription before giving up on this connection.

A stream that never subscribes would otherwise hold a client that believes it is
covered. Timing out closes the response so the browser reconnects."""


class RevealEventSource(Protocol):
    """The Valkey surface this service needs; satisfied by ``ValkeyRuntime``."""

    def iter_revelation_events(
        self,
        contest_id: str,
        scope: str,
        *,
        on_subscribed: Callable[[], None] | None = ...,
    ) -> AsyncGenerator[RevelationEvent]:
        """Yield ceremony frames, signalling when the subscription is live."""
        ...


async def iter_ready_then_events(
    runtime: RevealEventSource,
    contest_id: str,
    scope: str,
) -> AsyncGenerator[RevelationEvent | None]:
    """Yield ``None`` once the subscription is live, then each frame.

    The ``None`` sentinel is the coverage signal the route turns into a
    ``reveal_ready`` event. Keeping it a sentinel rather than a second yield type
    keeps this module free of any HTTP or SSE vocabulary.

    Args:
        runtime: The Valkey runtime (or a structural fake).
        contest_id: Contest whose ceremony channel to follow.
        scope: Canonical ceremony scope (a site id, or ``"global"``).

    Yields:
        ``None`` exactly once, then one event per published frame — a
        state-changed nudge or a transient media cue. The route decides which
        SSE event name each becomes; this module stays free of that vocabulary.
    """
    subscribed = asyncio.Event()
    queue: asyncio.Queue[RevelationEvent] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
    finished = asyncio.Event()

    async with aclosing(runtime.iter_revelation_events(contest_id, scope, on_subscribed=subscribed.set)) as events:

        async def pump() -> None:
            """Drain the subscription into the queue until it ends or is cancelled."""
            try:
                async for event in events:
                    if queue.full():
                        # Drop the oldest frame rather than block the subscriber:
                        # nothing on this channel carries state, so the newest
                        # frames are the ones worth keeping. A client this far
                        # behind is already reconciling from the store.
                        with suppress(asyncio.QueueEmpty):
                            queue.get_nowait()
                    queue.put_nowait(event)
            finally:
                # Generator termination is deliberately separate from successful
                # subscription. Treating both as "ready" would let a failed
                # SUBSCRIBE produce a false coverage signal.
                finished.set()

        pump_task = asyncio.create_task(pump())
        try:
            subscribed_wait = asyncio.create_task(subscribed.wait())
            finished_wait = asyncio.create_task(finished.wait())
            try:
                completed, _pending = await asyncio.wait(
                    {subscribed_wait, finished_wait},
                    timeout=_SUBSCRIBE_TIMEOUT_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not completed:
                    logger.warning(
                        "reveal subscription for contest=%s scope=%s did not become ready; closing stream",
                        contest_id,
                        scope,
                    )
                    return
                if finished.is_set():
                    # No live coverage exists. The SSE response closes without a
                    # ready event, and EventSource reconnects in the usual way.
                    return
            finally:
                for waiter in (subscribed_wait, finished_wait):
                    if not waiter.done():
                        waiter.cancel()
                await asyncio.gather(subscribed_wait, finished_wait, return_exceptions=True)

            if not subscribed.is_set():
                return

            # Coverage starts here: everything published from now on reaches the
            # queue, so the client's reconciliation cannot race the subscription.
            yield None

            while True:
                if finished.is_set() and queue.empty():
                    return
                getter = asyncio.ensure_future(queue.get())
                ender = asyncio.ensure_future(finished.wait())
                try:
                    await asyncio.wait({getter, ender}, return_when=asyncio.FIRST_COMPLETED)
                    if getter.done():
                        yield getter.result()
                        continue
                finally:
                    for pending in (getter, ender):
                        if not pending.done():
                            pending.cancel()
        finally:
            pump_task.cancel()
            with suppress(asyncio.CancelledError):
                await pump_task
