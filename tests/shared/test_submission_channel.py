#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Round-trip and malformed-payload tests for the submissions pub/sub channel."""

from __future__ import annotations

import asyncio
import contextlib
import os
from uuid import uuid4

import pytest
import valkey.asyncio as aivalkey

from shared.queue_schema import SubmissionEvent
from shared.services.valkey_service import _publish_submission_with_client
from shared.services.valkey_service.constants import (
    ARENA_RESULTS_CHANNEL,
    QUEUE_RESULTS_CHANNEL,
    QUEUE_SUBMISSIONS_CHANNEL,
    REVELATION_CHANNEL_PREFIX,
)
from shared.services.valkey_service.runtime import ValkeyRuntime
from web.config import settings


def _make_event() -> SubmissionEvent:
    return SubmissionEvent(
        submission_id=str(uuid4()),
        contest_id=str(uuid4()),
        team_id=str(uuid4()),
        problem_id=str(uuid4()),
    )


def test_pubsub_channels_use_the_test_worker_namespace() -> None:
    """Real-Valkey tests cannot publish onto application Pub/Sub channels."""
    namespace = os.environ["NOCA_TEST_VALKEY_CHANNEL_NAMESPACE"]
    assert f"{namespace}:judge:results" == QUEUE_RESULTS_CHANNEL
    assert f"{namespace}:judge:submissions" == QUEUE_SUBMISSIONS_CHANNEL
    assert f"{namespace}:arena:results" == ARENA_RESULTS_CHANNEL
    assert f"{namespace}:revelation:events" == REVELATION_CHANNEL_PREFIX


@pytest.mark.asyncio
async def test_submission_round_trips_through_channel(valkey_client: aivalkey.Valkey) -> None:
    """An event published with the helper is received by iter_submission_events."""
    runtime = ValkeyRuntime(valkey_url=settings.valkey_url, healthcheck_interval_s=60)
    await runtime.start()

    event = _make_event()
    agen = runtime.iter_submission_events()
    next_task = asyncio.create_task(agen.__anext__())
    try:
        received: SubmissionEvent | None = None
        for _ in range(20):
            await _publish_submission_with_client(valkey_client, event)
            done, _pending = await asyncio.wait({next_task}, timeout=0.1)
            if done:
                received = next_task.result()
                break
        assert received is not None, "Submission event was not received on the channel"
        assert received.submission_id == event.submission_id
        assert received.contest_id == event.contest_id
        assert received.team_id == event.team_id
        assert received.problem_id == event.problem_id
    finally:
        if not next_task.done():
            next_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                await next_task
        with contextlib.suppress(Exception):
            await agen.aclose()
        await runtime.stop()


@pytest.mark.asyncio
async def test_submission_test_channel_does_not_reach_production_subscriber(
    valkey_client: aivalkey.Valkey,
) -> None:
    """The test namespace isolates Pub/Sub even though logical databases do not."""
    production_pubsub = valkey_client.pubsub()
    await production_pubsub.subscribe("judge:submissions")
    await production_pubsub.get_message(ignore_subscribe_messages=False, timeout=1.0)
    try:
        await _publish_submission_with_client(valkey_client, _make_event())
        message = await production_pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
        assert message is None
    finally:
        await production_pubsub.unsubscribe("judge:submissions")
        await production_pubsub.aclose()


@pytest.mark.asyncio
async def test_malformed_submission_payload_is_skipped_not_fatal(
    valkey_client: aivalkey.Valkey,
) -> None:
    """A malformed message is logged and skipped; the subscriber keeps running."""
    runtime = ValkeyRuntime(valkey_url=settings.valkey_url, healthcheck_interval_s=60)
    await runtime.start()

    good = _make_event()
    agen = runtime.iter_submission_events()
    next_task = asyncio.create_task(agen.__anext__())
    try:
        received: SubmissionEvent | None = None
        for _ in range(20):
            # Interleave a malformed frame (missing required fields / not JSON) with a
            # valid one: the parse guard must drop the bad frame and still yield the good.
            await valkey_client.publish(QUEUE_SUBMISSIONS_CHANNEL, "not-json")
            await valkey_client.publish(QUEUE_SUBMISSIONS_CHANNEL, '{"submission_id": "x"}')
            await _publish_submission_with_client(valkey_client, good)
            done, _pending = await asyncio.wait({next_task}, timeout=0.1)
            if done:
                received = next_task.result()
                break
        assert received is not None, "Valid submission event was not received after malformed frames"
        assert received.submission_id == good.submission_id
    finally:
        if not next_task.done():
            next_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                await next_task
        with contextlib.suppress(Exception):
            await agen.aclose()
        await runtime.stop()
