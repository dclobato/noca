#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for the animator in-process event fan-out service.

These tests exercise the process-internal behavior only: contest filtering,
legacy-event drop, freeze redaction, timer ticks, overflow coalescing, disconnect
cleanup, monotonic ids, reconnect/backoff, and prompt shutdown. The HTTP-level
concerns (native SSE framing, heartbeat, ``Last-Event-ID`` no-replay, session
release) live in ``test_events_route.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from animator.models.query_records import ContestRecord
from animator.services.event_stream_service import (
    EVENT_SCOREBOARD_REFRESH,
    EVENT_SUBMISSION,
    EVENT_TIMER_TICK,
    EVENT_VERDICT,
    AnimatorEventStream,
    RedactedVerdictPayload,
    SubmissionPayload,
    TimerTickPayload,
    VerdictPayload,
    _ClientChannel,
    _StreamEvent,
)
from shared.queue_schema import SubmissionEvent, VerdictEvent

pytestmark = pytest.mark.asyncio


def _make_contest(
    contest_id: str,
    *,
    frozen: bool = False,
    final_released: bool = False,
) -> ContestRecord:
    """Build a contest whose freeze boundary sits before or after the real now.

    ``_dispatch_verdict`` evaluates freeze against wall-clock ``datetime.now``, so
    the fixture anchors its start relative to the real current time: a frozen
    contest started long ago with an early freeze, an open one started now with a
    freeze far in the future.
    """
    now = datetime.now(UTC)
    return ContestRecord(
        id=contest_id,
        login_slug=f"slug-{contest_id}",
        contest_name=f"Contest {contest_id}",
        animator_enabled=True,
        start_time=now - timedelta(hours=2) if frozen else now,
        duration_minutes=60 if final_released else 6000,
        stop_updating_scoreboard=1 if frozen else 6000,
        wa_penalty=20,
        accept_pe=False,
        ce_adds_penalty=False,
        release_scoreboard_after_end=final_released,
    )


def _make_verdict(*, contest_id: str | None = "c1", submission_id: str = "sub-1") -> VerdictEvent:
    """Build a finalized verdict event for the given contest."""
    return VerdictEvent(
        submission_id=submission_id,
        judgment_id="judg-1",
        verdict="AC",
        contest_id=contest_id,
        team_id="team-1",
        problem_id="prob-a",
        update_kind="autojudge",
    )


def _make_submission(*, contest_id: str = "c1", submission_id: str = "sub-1") -> SubmissionEvent:
    """Build a new-submission event for the given contest."""
    return SubmissionEvent(
        submission_id=submission_id,
        contest_id=contest_id,
        team_id="team-1",
        problem_id="prob-a",
    )


def _drain(channel: _ClientChannel) -> list[_StreamEvent]:
    """Pull every currently-queued event from a channel without blocking."""
    events: list[_StreamEvent] = []
    while True:
        try:
            events.append(channel._queue.get_nowait())
        except asyncio.QueueEmpty:
            return events


async def test_dispatch_filters_by_contest_and_orders_verdict_then_refresh() -> None:
    stream = AnimatorEventStream(_FakeRuntime([]))
    watching = stream.register(_make_contest("c1"))
    other = stream.register(_make_contest("c2"))

    stream._dispatch_verdict(_make_verdict(contest_id="c1"))

    watching_events = _drain(watching)
    assert [e.event for e in watching_events] == [EVENT_VERDICT, EVENT_SCOREBOARD_REFRESH]
    assert isinstance(watching_events[0].data, VerdictPayload)
    assert watching_events[0].data.judgment_id == "judg-1"
    assert watching_events[0].data.team_id == "team-1"
    # The other contest's channel receives nothing.
    assert _drain(other) == []


async def test_dispatch_drops_legacy_event_without_contest_id() -> None:
    stream = AnimatorEventStream(_FakeRuntime([]))
    channel = stream.register(_make_contest("c1"))

    stream._dispatch_verdict(_make_verdict(contest_id=None))

    assert _drain(channel) == []


async def test_dispatch_redacts_verdict_when_contest_frozen() -> None:
    stream = AnimatorEventStream(_FakeRuntime([]))
    channel = stream.register(_make_contest("c1", frozen=True))

    stream._dispatch_verdict(_make_verdict(contest_id="c1"))

    events = _drain(channel)
    assert [e.event for e in events] == [EVENT_VERDICT, EVENT_SCOREBOARD_REFRESH]
    assert isinstance(events[0].data, RedactedVerdictPayload)
    # No team/problem/verdict identifiers leak in the frozen payload.
    dumped = events[0].data.model_dump()
    assert dumped == {"redacted": True}


async def test_dispatch_submission_filters_by_contest_and_carries_payload() -> None:
    stream = AnimatorEventStream(_FakeRuntime([]))
    watching = stream.register(_make_contest("c1"))
    other = stream.register(_make_contest("c2"))

    stream._dispatch_submission(_make_submission(contest_id="c1", submission_id="sub-9"))

    events = _drain(watching)
    assert [e.event for e in events] == [EVENT_SUBMISSION]
    assert isinstance(events[0].data, SubmissionPayload)
    assert events[0].data.submission_id == "sub-9"
    assert events[0].data.team_id == "team-1"
    assert events[0].data.problem_id == "prob-a"
    # The other contest's channel receives nothing.
    assert _drain(other) == []


async def test_dispatch_submission_suppressed_when_frozen() -> None:
    stream = AnimatorEventStream(_FakeRuntime([]))
    channel = stream.register(_make_contest("c1", frozen=True))

    stream._dispatch_submission(_make_submission(contest_id="c1"))

    # No frame at all while frozen (no redacted variant).
    assert _drain(channel) == []


async def test_released_final_scoreboard_events_are_not_redacted() -> None:
    """An ended, released contest streams the same public state as its snapshot."""
    stream = AnimatorEventStream(_FakeRuntime([]))
    channel = stream.register(_make_contest("c1", frozen=True, final_released=True))

    stream._dispatch_verdict(_make_verdict(contest_id="c1"))
    stream._dispatch_submission(_make_submission(contest_id="c1"))

    events = _drain(channel)
    assert [event.event for event in events] == [
        EVENT_VERDICT,
        EVENT_SCOREBOARD_REFRESH,
        EVENT_SUBMISSION,
    ]
    assert isinstance(events[0].data, VerdictPayload)


async def test_start_launches_and_stop_cancels_all_three_tasks() -> None:
    stream = AnimatorEventStream(_BlockingRuntime(), timer_tick_seconds=1000.0)
    await stream.start()
    await asyncio.sleep(0)  # let the tasks start
    tasks = (stream._subscriber_task, stream._submission_subscriber_task, stream._timer_task)
    assert all(task is not None and not task.done() for task in tasks)

    await asyncio.wait_for(stream.stop(), timeout=2.0)
    assert stream._subscriber_task is None
    assert stream._submission_subscriber_task is None
    assert stream._timer_task is None
    assert all(task.cancelled() or task.done() for task in tasks)


async def test_timer_tick_broadcasts_to_all_clients() -> None:
    stream = AnimatorEventStream(_FakeRuntime([]))
    a = stream.register(_make_contest("c1"))
    b = stream.register(_make_contest("c2"))

    stream._broadcast_timer_tick()

    for channel in (a, b):
        events = _drain(channel)
        assert len(events) == 1
        assert events[0].event == EVENT_TIMER_TICK
        assert isinstance(events[0].data, TimerTickPayload)


async def test_multiple_clients_same_contest_all_receive_verdict() -> None:
    stream = AnimatorEventStream(_FakeRuntime([]))
    a = stream.register(_make_contest("c1"))
    b = stream.register(_make_contest("c1"))

    stream._dispatch_verdict(_make_verdict(contest_id="c1"))

    assert [e.event for e in _drain(a)] == [EVENT_VERDICT, EVENT_SCOREBOARD_REFRESH]
    assert [e.event for e in _drain(b)] == [EVENT_VERDICT, EVENT_SCOREBOARD_REFRESH]


async def test_overflow_coalesces_to_single_pending_refresh_and_drops_timer() -> None:
    channel = _ClientChannel(_make_contest("c1"))
    tick = _StreamEvent(EVENT_TIMER_TICK, TimerTickPayload(server_time="t"), "0")
    # Fill the bounded queue to capacity with timer ticks.
    while True:
        try:
            channel._queue.put_nowait(tick)
        except asyncio.QueueFull:
            break
    assert channel._queue.full()

    # A verdict overflow coalesces to exactly one pending refresh.
    channel.offer(_StreamEvent(EVENT_VERDICT, RedactedVerdictPayload(), "x"))
    assert channel.take_pending_refresh() is True
    assert channel.take_pending_refresh() is False

    # A refresh overflow also coalesces to exactly one pending refresh.
    channel.offer(_StreamEvent(EVENT_SCOREBOARD_REFRESH, RedactedVerdictPayload(), "y"))
    channel.offer(_StreamEvent(EVENT_SCOREBOARD_REFRESH, RedactedVerdictPayload(), "z"))
    assert channel.take_pending_refresh() is True
    assert channel.take_pending_refresh() is False

    # A timer-tick overflow is dropped and never sets the pending flag.
    channel.offer(_StreamEvent(EVENT_TIMER_TICK, TimerTickPayload(server_time="t"), "w"))
    assert channel.take_pending_refresh() is False


async def test_register_and_unregister_track_client_count() -> None:
    stream = AnimatorEventStream(_FakeRuntime([]))
    assert stream.client_count == 0
    first = stream.register(_make_contest("c1"))
    second = stream.register(_make_contest("c1"))
    assert first is not second
    assert stream.client_count == 2
    stream.unregister(first)
    assert stream.client_count == 1
    # Unregister is idempotent.
    stream.unregister(first)
    assert stream.client_count == 1
    stream.unregister(second)
    assert stream.client_count == 0


async def test_reconnect_registers_fresh_channel_with_no_replay() -> None:
    stream = AnimatorEventStream(_FakeRuntime([]))
    first = stream.register(_make_contest("c1"))
    stream._dispatch_verdict(_make_verdict(contest_id="c1"))
    assert len(_drain(first)) == 2
    stream.unregister(first)

    # A reconnection is a brand-new channel that replays no history.
    second = stream.register(_make_contest("c1"))
    assert second is not first
    assert _drain(second) == []


async def test_event_ids_are_monotonic_across_verdicts_and_ticks() -> None:
    stream = AnimatorEventStream(_FakeRuntime([]))
    channel = stream.register(_make_contest("c1"))

    stream._dispatch_verdict(_make_verdict(contest_id="c1"))
    stream._broadcast_timer_tick()
    stream._dispatch_verdict(_make_verdict(contest_id="c1", submission_id="sub-2"))

    ids = [int(e.id) for e in _drain(channel)]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    # next_event_id keeps advancing past the highest emitted id.
    assert int(stream.next_event_id()) > max(ids)


async def test_subscriber_resets_backoff_after_delivery_and_respects_ceiling() -> None:
    # First reconnect delivers an event (resets backoff), later ones deliver
    # nothing so the backoff climbs and saturates at the ceiling.
    runtime = _FakeRuntime([[_make_verdict(contest_id="c1")], [], [], [], []])
    stream = AnimatorEventStream(runtime, backoff_base_seconds=1.0, backoff_max_seconds=4.0)
    stream.register(_make_contest("c1"))

    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> bool:
        recorded.append(seconds)
        if len(recorded) >= 5:
            stream._stop_event.set()
            return True
        return False

    stream._sleep_or_stopped = fake_sleep  # type: ignore[method-assign]
    await stream._run_subscriber()

    # After the delivering batch backoff is base (1.0); with no further delivery it
    # doubles (2.0, 4.0) and then saturates at the ceiling (4.0, 4.0).
    assert recorded == [1.0, 2.0, 4.0, 4.0, 4.0]


async def test_sleep_or_stopped_wakes_immediately_on_shutdown() -> None:
    stream = AnimatorEventStream(_FakeRuntime([]))
    stream._stop_event.set()
    # A long interval returns at once because the stop event is already set; this
    # is the same helper both the backoff and timer waits use.
    result = await asyncio.wait_for(stream._sleep_or_stopped(1000.0), timeout=1.0)
    assert result is True


async def test_start_and_stop_are_prompt_with_blocking_subscriber() -> None:
    runtime = _BlockingRuntime()
    stream = AnimatorEventStream(runtime, timer_tick_seconds=1000.0)
    await stream.start()
    await asyncio.sleep(0)  # let the tasks start
    # Even with a subscriber blocked on iter and a 1000 s timer wait, shutdown
    # completes promptly.
    await asyncio.wait_for(stream.stop(), timeout=2.0)


async def test_subscriber_delivers_scripted_events_through_run_loop() -> None:
    runtime = _FakeRuntime([[_make_verdict(contest_id="c1")]])
    stream = AnimatorEventStream(runtime, backoff_base_seconds=0.01, backoff_max_seconds=0.02)
    channel = stream.register(_make_contest("c1"))
    await stream.start()
    try:
        first = await asyncio.wait_for(channel.get(), timeout=1.0)
        second = await asyncio.wait_for(channel.get(), timeout=1.0)
    finally:
        await stream.stop()
    assert [first.event, second.event] == [EVENT_VERDICT, EVENT_SCOREBOARD_REFRESH]


class _FakeRuntime:
    """Fake ValkeyRuntime whose iter_verdict_events replays scripted batches.

    Each call consumes the next batch (a list of events), yields them, then ends —
    mirroring how ``iter_verdict_events`` returns on a recoverable disconnect so the
    subscriber backs off and reconnects. Once batches are exhausted, later calls
    yield nothing.
    """

    def __init__(
        self,
        batches: list[list[VerdictEvent]],
        submission_batches: list[list[SubmissionEvent]] | None = None,
    ) -> None:
        self._batches = list(batches)
        self._submission_batches = list(submission_batches or [])
        self.connect_count = 0

    async def iter_verdict_events(self) -> AsyncIterator[VerdictEvent]:
        self.connect_count += 1
        batch = self._batches.pop(0) if self._batches else []
        for event in batch:
            yield event

    async def iter_submission_events(self) -> AsyncIterator[SubmissionEvent]:
        batch = self._submission_batches.pop(0) if self._submission_batches else []
        for event in batch:
            yield event


class _BlockingRuntime:
    """Fake runtime whose subscriber iterators block until cancelled."""

    async def iter_verdict_events(self) -> AsyncIterator[VerdictEvent]:
        await asyncio.Event().wait()
        yield  # pragma: no cover - unreachable, keeps this an async generator

    async def iter_submission_events(self) -> AsyncIterator[SubmissionEvent]:
        await asyncio.Event().wait()
        yield  # pragma: no cover - unreachable, keeps this an async generator
