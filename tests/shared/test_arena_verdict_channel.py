#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Round-trip test for the Arena verdict pub/sub channel."""

from __future__ import annotations

import asyncio
import contextlib
from uuid import uuid4

import pytest
import valkey.asyncio as aivalkey

from shared.queue_schema import ArenaVerdictEvent
from shared.services.valkey_service import _publish_arena_verdict_with_client
from shared.services.valkey_service.runtime import ValkeyRuntime
from web.config import settings


@pytest.mark.asyncio
async def test_arena_verdict_round_trips_through_channel(valkey_client: aivalkey.Valkey) -> None:
    """An event published with the helper is received by iter_arena_verdict_events."""
    runtime = ValkeyRuntime(valkey_url=settings.valkey_url, healthcheck_interval_s=60)
    await runtime.start()

    event = ArenaVerdictEvent(submission_id=str(uuid4()), judgment_id=str(uuid4()), verdict="AC")

    agen = runtime.iter_arena_verdict_events()
    next_task = asyncio.create_task(agen.__anext__())
    try:
        # Give the subscriber time to establish its subscription, then publish
        # repeatedly until the event is delivered (no synchronous subscribe ack here).
        received: ArenaVerdictEvent | None = None
        for _ in range(20):
            await _publish_arena_verdict_with_client(valkey_client, event)
            done, _pending = await asyncio.wait({next_task}, timeout=0.1)
            if done:
                received = next_task.result()
                break
        assert received is not None, "Arena verdict event was not received on the channel"
        assert received.submission_id == event.submission_id
        assert received.judgment_id == event.judgment_id
        assert received.verdict == "AC"
    finally:
        if not next_task.done():
            next_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                await next_task
        with contextlib.suppress(Exception):
            await agen.aclose()
        await runtime.stop()
