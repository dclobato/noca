#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""In-process live-event fan-out for the animator SSE stream.

One :class:`AnimatorEventStream` per animator process runs Valkey subscribers over
``judge:results`` and ``judge:submissions`` plus a bounded-cadence timer, fanning
them out to per-client bounded queues. Browser SSE clients register a
:class:`_ClientChannel`, drain typed :class:`_StreamEvent` values, and unregister
on disconnect. PostgreSQL snapshots stay authoritative: the stream only carries
change notifications, so a slow or dropped client can lose *detail* but never the
eventual authoritative ``scoreboard_refresh``.

Design decisions (see ``docs/noca-animator/pre-flight.md``):

* **Legacy events** (``contest_id is None``) are dropped and logged, never
  broadcast — current publishers always populate ``contest_id``.
* **Overflow** never evicts queued items. A full queue coalesces any dropped
  ``verdict``, ``submission``, or ``scoreboard_refresh`` into a single
  pending-refresh boolean and drops ``timer_tick`` payloads, so memory is bounded
  and exactly one authoritative refresh always survives.
* **Heartbeat** is owned by FastAPI's native SSE layer (a 15 s idle-only comment),
  not by this service.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel

from animator.models.query_records import ContestRecord
from animator.models.responses import (
    RedactedVerdictPayload,
    ScoreboardRefreshPayload,
    SubmissionPayload,
    TimerTickPayload,
    VerdictPayload,
)
from shared.queue_schema import SubmissionEvent, VerdictEvent
from shared.services.valkey_service import ValkeyRuntime
from shared.services.valkey_service.errors import is_recoverable_valkey_error

__all__ = [
    "EVENT_SCOREBOARD_REFRESH",
    "EVENT_SUBMISSION",
    "EVENT_TIMER_TICK",
    "EVENT_VERDICT",
    "AnimatorEventStream",
    "RedactedVerdictPayload",
    "ScoreboardRefreshPayload",
    "SubmissionPayload",
    "TimerTickPayload",
    "VerdictPayload",
]

logger = logging.getLogger(__name__)

# Bounded per-client queue capacity. Overflow coalesces to a single pending
# refresh rather than growing memory (see module docstring).
_CLIENT_QUEUE_MAXSIZE = 64
# Bounded cadence for server timer ticks.
_TIMER_TICK_SECONDS = 10.0
# Exponential-backoff bounds for reconnecting the Valkey subscriber.
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 30.0

# SSE ``event:`` names emitted to clients.
EVENT_VERDICT = "verdict"
EVENT_SUBMISSION = "submission"
EVENT_SCOREBOARD_REFRESH = "scoreboard_refresh"
EVENT_TIMER_TICK = "timer_tick"


@dataclass(frozen=True)
class _StreamEvent:
    """One typed event queued for a single client channel.

    Attributes:
        event: SSE ``event:`` name.
        data: Typed payload serialized into the SSE ``data:`` field.
        id: Monotonic process-scoped event id.
    """

    event: str
    data: BaseModel
    id: str


class _ClientChannel:
    """A bounded per-client queue plus a single coalesced-refresh flag."""

    def __init__(self, contest: ContestRecord) -> None:
        """Bind the channel to one contest for freeze-aware filtering.

        Args:
            contest: The animator-enabled contest this client is watching.
        """
        self.contest = contest
        self._queue: asyncio.Queue[_StreamEvent] = asyncio.Queue(maxsize=_CLIENT_QUEUE_MAXSIZE)
        self._pending_refresh = False

    def offer(self, event: _StreamEvent) -> None:
        """Enqueue an event without blocking, coalescing on overflow.

        A full queue never evicts an already-queued item. Instead any non-timer
        event sets the single pending-refresh flag (so at most one refresh is ever
        pending) and a ``timer_tick`` is dropped.

        Args:
            event: The event to deliver to this client.
        """
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            if event.event != EVENT_TIMER_TICK:
                self._pending_refresh = True

    def take_pending_refresh(self) -> bool:
        """Return and clear the pending-refresh flag.

        Returns:
            True exactly once per coalesced overflow, so the consumer emits one
            authoritative ``scoreboard_refresh`` after draining the queue.
        """
        if self._pending_refresh:
            self._pending_refresh = False
            return True
        return False

    async def get(self) -> _StreamEvent:
        """Await the next queued event for this client."""
        return await self._queue.get()


class AnimatorEventStream:
    """Process-wide Valkey subscriber and timer fanning out to SSE clients."""

    def __init__(
        self,
        runtime: ValkeyRuntime,
        *,
        timer_tick_seconds: float = _TIMER_TICK_SECONDS,
        backoff_base_seconds: float = _BACKOFF_BASE_SECONDS,
        backoff_max_seconds: float = _BACKOFF_MAX_SECONDS,
    ) -> None:
        """Create the fan-out service.

        Args:
            runtime: Shared Valkey runtime exposing ``iter_verdict_events``.
            timer_tick_seconds: Bounded cadence for ``timer_tick`` emission.
            backoff_base_seconds: Initial reconnect backoff.
            backoff_max_seconds: Reconnect backoff ceiling.
        """
        self._runtime = runtime
        self._timer_tick_seconds = timer_tick_seconds
        self._backoff_base_seconds = backoff_base_seconds
        self._backoff_max_seconds = backoff_max_seconds
        self._channels: set[_ClientChannel] = set()
        self._stop_event = asyncio.Event()
        self._subscriber_task: asyncio.Task[None] | None = None
        self._submission_subscriber_task: asyncio.Task[None] | None = None
        self._timer_task: asyncio.Task[None] | None = None
        self._id_counter = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Start the subscriber and timer background tasks."""
        self._stop_event.clear()
        self._subscriber_task = asyncio.create_task(self._run_subscriber(), name="animator-event-subscriber")
        self._submission_subscriber_task = asyncio.create_task(
            self._run_submission_subscriber(), name="animator-submission-subscriber"
        )
        self._timer_task = asyncio.create_task(self._run_timer(), name="animator-event-timer")

    async def stop(self) -> None:
        """Signal shutdown and await both background tasks.

        Setting the stop event interrupts the backoff and timer sleeps promptly;
        task cancellation is the backstop for a task blocked elsewhere. A task that
        finished by re-raising an unrecoverable error re-raises here, so callers
        must run their own cleanup in a ``finally``.
        """
        self._stop_event.set()
        for task in (self._subscriber_task, self._submission_subscriber_task, self._timer_task):
            if task is None:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._subscriber_task = None
        self._submission_subscriber_task = None
        self._timer_task = None

    # ------------------------------------------------------------------
    # Client registration
    # ------------------------------------------------------------------
    def register(self, contest: ContestRecord) -> _ClientChannel:
        """Register a fresh channel for one connected client.

        Args:
            contest: The contest the client is watching.

        Returns:
            A new, independent channel. Each reconnection registers a fresh one;
            there is no replay of historical events.
        """
        channel = _ClientChannel(contest)
        self._channels.add(channel)
        return channel

    def unregister(self, channel: _ClientChannel) -> None:
        """Remove a channel once its client disconnects.

        Args:
            channel: The channel to drop; idempotent if already removed.
        """
        self._channels.discard(channel)

    @property
    def client_count(self) -> int:
        """Number of currently registered client channels."""
        return len(self._channels)

    def next_event_id(self) -> str:
        """Return the next monotonically increasing process-scoped event id."""
        self._id_counter += 1
        return str(self._id_counter)

    # ------------------------------------------------------------------
    # Background loops
    # ------------------------------------------------------------------
    async def _run_subscriber(self) -> None:
        """Subscribe to verdict events, reconnecting with bounded backoff."""
        backoff = self._backoff_base_seconds
        while not self._stop_event.is_set():
            try:
                async for event in self._runtime.iter_verdict_events():
                    if self._stop_event.is_set():
                        break
                    self._dispatch_verdict(event)
                    backoff = self._backoff_base_seconds
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not is_recoverable_valkey_error(exc):
                    logger.exception("Animator verdict subscriber failed unrecoverably")
                    raise
                logger.warning("Animator verdict subscriber interrupted: %s", exc)
            if self._stop_event.is_set():
                return
            if await self._sleep_or_stopped(backoff):
                return
            backoff = min(backoff * 2, self._backoff_max_seconds)

    async def _run_submission_subscriber(self) -> None:
        """Subscribe to submission events, reconnecting with bounded backoff."""
        backoff = self._backoff_base_seconds
        while not self._stop_event.is_set():
            try:
                async for event in self._runtime.iter_submission_events():
                    if self._stop_event.is_set():
                        break
                    self._dispatch_submission(event)
                    backoff = self._backoff_base_seconds
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not is_recoverable_valkey_error(exc):
                    logger.exception("Animator submission subscriber failed unrecoverably")
                    raise
                logger.warning("Animator submission subscriber interrupted: %s", exc)
            if self._stop_event.is_set():
                return
            if await self._sleep_or_stopped(backoff):
                return
            backoff = min(backoff * 2, self._backoff_max_seconds)

    async def _run_timer(self) -> None:
        """Emit ``timer_tick`` to every channel at the bounded cadence."""
        while not self._stop_event.is_set():
            self._broadcast_timer_tick()
            if await self._sleep_or_stopped(self._timer_tick_seconds):
                return

    async def _sleep_or_stopped(self, seconds: float) -> bool:
        """Sleep up to ``seconds`` or until shutdown is signalled.

        Args:
            seconds: Maximum time to wait.

        Returns:
            True when the stop event fired during the wait, else False on timeout.
        """
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except TimeoutError:
            return False
        return True

    # ------------------------------------------------------------------
    # Fan-out
    # ------------------------------------------------------------------
    def _dispatch_verdict(self, event: VerdictEvent) -> None:
        """Fan one verdict out to matching channels, freeze-redacting per contest.

        Legacy events without a ``contest_id`` are dropped and logged. For each
        matching channel a ``verdict`` (redacted while frozen) is enqueued
        immediately before its ``scoreboard_refresh`` so clients observe the two
        in order when the queue is not saturated.

        Args:
            event: The finalized verdict event from Valkey.
        """
        if event.contest_id is None:
            logger.warning(
                "Dropping legacy verdict event without contest_id (submission=%s)",
                event.submission_id,
            )
            return
        now = datetime.now(UTC)
        for channel in list(self._channels):
            if channel.contest.id != event.contest_id:
                continue
            payload: BaseModel
            if channel.contest.is_frozen_at(now):
                payload = RedactedVerdictPayload()
            else:
                payload = VerdictPayload(
                    submission_id=event.submission_id,
                    judgment_id=event.judgment_id,
                    problem_id=event.problem_id,
                    team_id=event.team_id,
                    verdict=event.verdict,
                    update_kind=event.update_kind,
                )
            channel.offer(_StreamEvent(EVENT_VERDICT, payload, self.next_event_id()))
            channel.offer(_StreamEvent(EVENT_SCOREBOARD_REFRESH, ScoreboardRefreshPayload(), self.next_event_id()))

    def _dispatch_submission(self, event: SubmissionEvent) -> None:
        """Fan one submission nudge out to matching, non-frozen channels.

        The event is a low-latency signal only: the client reacts by refetching
        the authoritative ``/snapshot`` (whose ``pending_submissions`` list is
        freeze-safe) and flashing the cell. It is suppressed entirely while a
        contest is frozen, so no post-freeze activity leaks, and no paired
        ``scoreboard_refresh`` is enqueued — the client's ``submission`` handler
        triggers the refetch, and queue overflow already coalesces a dropped
        ``submission`` into the single pending-refresh flag.

        Args:
            event: The new-submission event from Valkey.
        """
        now = datetime.now(UTC)
        for channel in list(self._channels):
            if channel.contest.id != event.contest_id:
                continue
            if channel.contest.is_frozen_at(now):
                continue
            payload = SubmissionPayload(
                submission_id=event.submission_id,
                team_id=event.team_id,
                problem_id=event.problem_id,
            )
            channel.offer(_StreamEvent(EVENT_SUBMISSION, payload, self.next_event_id()))

    def _broadcast_timer_tick(self) -> None:
        """Enqueue a ``timer_tick`` carrying current UTC server time to all."""
        payload = TimerTickPayload(server_time=datetime.now(UTC).isoformat())
        for channel in list(self._channels):
            channel.offer(_StreamEvent(EVENT_TIMER_TICK, payload, self.next_event_id()))
