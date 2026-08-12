#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the animator SSE event stream (`/c/{slug}/events`).

httpx's ASGITransport buffers the whole response body, so it cannot consume an
open-ended SSE stream (the repo's other SSE tests avoid HTTP streaming for the
same reason). These tests therefore drive the ASGI app directly: a controllable
``receive`` channel lets the test deliver ``http.disconnect`` to end the stream,
and a ``send`` collector parses the real native SSE wire frames. This is the true
route boundary — native typed framing, FastAPI's idle-only heartbeat, and
structured disconnect teardown all run for real.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import fastapi.routing
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from animator.error_handlers import register_error_handlers
from animator.routes.public import router as public_router
from animator.services.event_stream_service import AnimatorEventStream
from shared.queue_schema import SubmissionEvent, VerdictEvent
from web.models.contest import Contest
from web.models.users import UberAdmin

pytestmark = pytest.mark.asyncio


class _BlockingRuntime:
    """Fake runtime whose iter_verdict_events blocks until cancelled."""

    async def iter_verdict_events(self) -> AsyncIterator[VerdictEvent]:
        await asyncio.Event().wait()
        yield  # pragma: no cover - unreachable, keeps this an async generator


def _build_app(engine: AsyncEngine, stream: AnimatorEventStream) -> FastAPI:
    """Wire a minimal app around the public router with a live event stream."""
    app = FastAPI()
    app.state.db_session = async_sessionmaker(engine, expire_on_commit=False)
    app.state.event_stream = stream
    app.include_router(public_router)
    # Register the real handlers: the indistinguishability guarantee below is a
    # property of the responses this app actually produces, so a bare FastAPI
    # here would assert the framework default rather than what ships.
    register_error_handlers(app)
    return app


async def _make_contest(
    session: Any, uberadmin: UberAdmin, *, slug: str, frozen: bool, enabled: bool = True
) -> Contest:
    """Create and commit an enabled/disabled, frozen/open contest row."""
    now = datetime.now(UTC)
    contest = Contest(
        contest_name="Events Contest",
        contest_url="http://events.example.com",
        login_slug=slug,
        start_time=now - timedelta(hours=2) if frozen else now,
        duration_minutes=6000,
        stop_answers_after=6000,
        stop_updating_scoreboard=1 if frozen else 6000,
        clarifications_timeout_minutes=10,
        wa_penalty=20,
        accept_pe=False,
        ce_adds_penalty=False,
        animator_enabled=enabled,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(contest)
    await session.commit()
    return contest


def _make_verdict(contest_id: str, *, submission_id: str = "sub-1") -> VerdictEvent:
    """Build a finalized verdict event for the contest."""
    return VerdictEvent(
        submission_id=submission_id,
        judgment_id="judg-1",
        verdict="AC",
        contest_id=contest_id,
        team_id="team-1",
        problem_id="prob-a",
        update_kind="autojudge",
    )


def _make_submission(contest_id: str, *, submission_id: str = "sub-1") -> SubmissionEvent:
    """Build a new-submission event for the contest."""
    return SubmissionEvent(
        submission_id=submission_id,
        contest_id=contest_id,
        team_id="team-1",
        problem_id="prob-a",
    )


class _ASGIStream:
    """Drive one streaming ASGI request with a controllable disconnect.

    The request is started as a background task; body chunks are captured as the
    native SSE producer emits them. The test ends the stream by sending
    ``http.disconnect`` through ``receive``.
    """

    def __init__(self, app: FastAPI, path: str, *, headers: dict[str, str] | None = None) -> None:
        self._app = app
        header_pairs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        self._scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "scheme": "http",
            "headers": header_pairs,
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        self._incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._chunks: asyncio.Queue[bytes] = asyncio.Queue()
        self._started = asyncio.Event()
        self.status: int | None = None
        self._buffer = b""
        self._task: asyncio.Task[None] | None = None

    async def _receive(self) -> dict[str, Any]:
        return await self._incoming.get()

    async def _send(self, message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self._started.set()
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body:
                self._chunks.put_nowait(body)

    async def __aenter__(self) -> _ASGIStream:
        self._incoming.put_nowait({"type": "http.request", "body": b"", "more_body": False})
        self._task = asyncio.create_task(self._app(self._scope, self._receive, self._send))
        await asyncio.wait_for(self._started.wait(), timeout=2.0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._task is not None
        if not self._task.done():
            self._incoming.put_nowait({"type": "http.disconnect"})
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._task, timeout=2.0)

    async def disconnect(self) -> None:
        """Deliver ``http.disconnect`` and await the request task to finish."""
        assert self._task is not None
        self._incoming.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(self._task, timeout=2.0)

    def _drain_buffered(self, events: list[str]) -> None:
        """Split any complete frames already in the buffer into ``events``."""
        while b"\n\n" in self._buffer:
            raw, self._buffer = self._buffer.split(b"\n\n", 1)
            if raw.strip():
                events.append(raw.decode())

    async def read_events(self, count: int, *, timeout: float = 3.0) -> list[str]:
        """Read ``count`` complete SSE frames (each a ``\\n\\n``-terminated block)."""
        events: list[str] = []
        async with asyncio.timeout(timeout):
            while len(events) < count:
                self._buffer += await self._chunks.get()
                self._drain_buffered(events)
        return events

    async def read_within(self, window: float) -> list[str]:
        """Return every frame that arrives within ``window`` seconds (maybe none)."""
        events: list[str] = []
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(window):
                while True:
                    self._buffer += await self._chunks.get()
                    self._drain_buffered(events)
        return events


def _parse_event(frame: str) -> dict[str, str]:
    """Parse an ``event:``/``data:``/``id:`` frame into a small dict."""
    parsed: dict[str, str] = {}
    for line in frame.splitlines():
        if line.startswith("event:"):
            parsed["event"] = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            parsed["data"] = line.removeprefix("data:").strip()
        elif line.startswith("id:"):
            parsed["id"] = line.removeprefix("id:").strip()
        elif line.startswith(":"):
            parsed.setdefault("comment", line.removeprefix(":").strip())
    return parsed


async def _wait_for_client(stream: AnimatorEventStream, *, count: int = 1) -> None:
    """Wait until the stream has registered ``count`` client channels."""
    async with asyncio.timeout(2.0):
        while stream.client_count < count:
            await asyncio.sleep(0.01)


async def test_verdict_streams_typed_payload_then_refresh(session: Any, uberadmin: UberAdmin) -> None:
    contest = await _make_contest(session, uberadmin, slug="ev-open", frozen=False)
    stream = AnimatorEventStream(_BlockingRuntime())  # type: ignore[arg-type]
    app = _build_app(session.bind, stream)

    async with _ASGIStream(app, "/c/ev-open/events") as conn:
        assert conn.status == 200
        await _wait_for_client(stream)
        stream._dispatch_verdict(_make_verdict(contest.id))
        frames = [_parse_event(f) for f in await conn.read_events(2)]

    verdict, refresh = frames[0], frames[1]
    assert verdict["event"] == "verdict"
    payload = json.loads(verdict["data"])
    assert payload["judgment_id"] == "judg-1"
    assert payload["team_id"] == "team-1"
    assert payload["verdict"] == "AC"
    assert "compile_log" not in payload
    assert refresh["event"] == "scoreboard_refresh"
    assert json.loads(refresh["data"]) == {}
    assert int(refresh["id"]) > int(verdict["id"])


async def test_verdict_redacted_when_contest_frozen(session: Any, uberadmin: UberAdmin) -> None:
    contest = await _make_contest(session, uberadmin, slug="ev-frozen", frozen=True)
    stream = AnimatorEventStream(_BlockingRuntime())  # type: ignore[arg-type]
    app = _build_app(session.bind, stream)

    async with _ASGIStream(app, "/c/ev-frozen/events") as conn:
        await _wait_for_client(stream)
        stream._dispatch_verdict(_make_verdict(contest.id))
        frames = [_parse_event(f) for f in await conn.read_events(1)]

    assert frames[0]["event"] == "verdict"
    assert json.loads(frames[0]["data"]) == {"redacted": True}


async def test_submission_streams_typed_payload(session: Any, uberadmin: UberAdmin) -> None:
    contest = await _make_contest(session, uberadmin, slug="ev-sub", frozen=False)
    stream = AnimatorEventStream(_BlockingRuntime())  # type: ignore[arg-type]
    app = _build_app(session.bind, stream)

    async with _ASGIStream(app, "/c/ev-sub/events") as conn:
        assert conn.status == 200
        await _wait_for_client(stream)
        stream._dispatch_submission(_make_submission(contest.id, submission_id="sub-9"))
        frames = [_parse_event(f) for f in await conn.read_events(1)]

    assert frames[0]["event"] == "submission"
    payload = json.loads(frames[0]["data"])
    assert payload == {"submission_id": "sub-9", "team_id": "team-1", "problem_id": "prob-a"}


async def test_submission_suppressed_when_contest_frozen(session: Any, uberadmin: UberAdmin) -> None:
    contest = await _make_contest(session, uberadmin, slug="ev-sub-frozen", frozen=True)
    stream = AnimatorEventStream(_BlockingRuntime())  # type: ignore[arg-type]
    app = _build_app(session.bind, stream)

    async with _ASGIStream(app, "/c/ev-sub-frozen/events") as conn:
        await _wait_for_client(stream)
        # A frozen contest emits no submission frame at all; a following timer tick
        # is the first frame the client sees, proving the submission was dropped.
        stream._dispatch_submission(_make_submission(contest.id))
        stream._broadcast_timer_tick()
        frames = [_parse_event(f) for f in await conn.read_events(1)]

    assert frames[0]["event"] == "timer_tick"


async def test_timer_tick_streams_server_time(session: Any, uberadmin: UberAdmin) -> None:
    await _make_contest(session, uberadmin, slug="ev-timer", frozen=False)
    stream = AnimatorEventStream(_BlockingRuntime())  # type: ignore[arg-type]
    app = _build_app(session.bind, stream)

    async with _ASGIStream(app, "/c/ev-timer/events") as conn:
        await _wait_for_client(stream)
        stream._broadcast_timer_tick()
        frames = [_parse_event(f) for f in await conn.read_events(1)]

    assert frames[0]["event"] == "timer_tick"
    assert "server_time" in json.loads(frames[0]["data"])


async def test_missing_and_disabled_are_indistinguishable_404(session: Any, uberadmin: UberAdmin) -> None:
    """An unknown slug and a disabled contest must be impossible to tell apart.

    Compares the two responses to each other rather than each to a literal, so
    the guarantee is asserted directly: any future divergence in status, body,
    or content type fails here regardless of what the body happens to be.
    """
    await _make_contest(session, uberadmin, slug="ev-disabled", frozen=False, enabled=False)
    stream = AnimatorEventStream(_BlockingRuntime())  # type: ignore[arg-type]
    app = _build_app(session.bind, stream)

    # The 404 comes from the dependency before any streaming, so plain httpx works.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unknown = await client.get("/c/ev-unknown/events")
        disabled = await client.get("/c/ev-disabled/events")

    assert unknown.status_code == disabled.status_code == 404
    assert unknown.content == disabled.content
    assert unknown.headers.get("content-type") == disabled.headers.get("content-type")
    # And the shared body must not name the framework.
    assert unknown.json() == {"error": "not_found"}


async def test_disconnect_unregisters_channel(session: Any, uberadmin: UberAdmin) -> None:
    await _make_contest(session, uberadmin, slug="ev-disc", frozen=False)
    stream = AnimatorEventStream(_BlockingRuntime())  # type: ignore[arg-type]
    app = _build_app(session.bind, stream)

    async with _ASGIStream(app, "/c/ev-disc/events") as conn:
        await _wait_for_client(stream)
        assert stream.client_count == 1
        await conn.disconnect()

    async with asyncio.timeout(2.0):
        while stream.client_count != 0:
            await asyncio.sleep(0.01)
    assert stream.client_count == 0


async def test_stream_holds_no_database_connection(session: Any, uberadmin: UberAdmin) -> None:
    contest = await _make_contest(session, uberadmin, slug="ev-nodb", frozen=False)
    engine: AsyncEngine = session.bind
    stream = AnimatorEventStream(_BlockingRuntime())  # type: ignore[arg-type]
    app = _build_app(engine, stream)

    async with _ASGIStream(app, "/c/ev-nodb/events") as conn:
        await _wait_for_client(stream)
        # The route resolves the contest through the detached dependency, which
        # closes its session before streaming, so an actively connected SSE stream
        # pins no pooled PostgreSQL connection.
        assert engine.sync_engine.pool.checkedout() == 0
        # A verdict still flows while the pool stays released.
        stream._dispatch_verdict(_make_verdict(contest.id))
        frames = [_parse_event(f) for f in await conn.read_events(2)]
        assert [f["event"] for f in frames] == ["verdict", "scoreboard_refresh"]
        assert engine.sync_engine.pool.checkedout() == 0


async def test_last_event_id_does_not_replay_history(session: Any, uberadmin: UberAdmin) -> None:
    contest = await _make_contest(session, uberadmin, slug="ev-lastid", frozen=False)
    stream = AnimatorEventStream(_BlockingRuntime())  # type: ignore[arg-type]
    app = _build_app(session.bind, stream)

    # Emit a verdict with no client connected: nothing is retained anywhere.
    stream._dispatch_verdict(_make_verdict(contest.id, submission_id="old"))

    async with _ASGIStream(app, "/c/ev-lastid/events", headers={"Last-Event-ID": "1"}) as conn:
        await _wait_for_client(stream)
        # A fresh connection sending Last-Event-ID replays nothing; it only sees
        # newly emitted events.
        stream._dispatch_verdict(_make_verdict(contest.id, submission_id="new"))
        frames = [_parse_event(f) for f in await conn.read_events(1)]

    assert frames[0]["event"] == "verdict"
    assert json.loads(frames[0]["data"])["submission_id"] == "new"


async def test_idle_stream_emits_comment_heartbeat(
    session: Any, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _make_contest(session, uberadmin, slug="ev-hb", frozen=False)
    # Shrink the native idle heartbeat interval so the test does not wait 15 s.
    monkeypatch.setattr(fastapi.routing, "_PING_INTERVAL", 0.15)
    stream = AnimatorEventStream(_BlockingRuntime())  # type: ignore[arg-type]
    app = _build_app(session.bind, stream)

    async with _ASGIStream(app, "/c/ev-hb/events") as conn:
        await _wait_for_client(stream)
        # An idle stream (no dispatched data) emits a comment-only keepalive.
        idle = _parse_event((await conn.read_events(1))[0])
        assert idle.get("comment") == "ping"


async def test_data_event_resets_the_idle_heartbeat_interval(
    session: Any, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    contest = await _make_contest(session, uberadmin, slug="ev-hb-reset", frozen=False)
    interval = 0.4
    monkeypatch.setattr(fastapi.routing, "_PING_INTERVAL", interval)
    # No timer/subscriber is started, so the only frames are the ones dispatched
    # here plus native heartbeats — letting us time the idle interval precisely.
    stream = AnimatorEventStream(_BlockingRuntime())  # type: ignore[arg-type]
    app = _build_app(session.bind, stream)

    async with _ASGIStream(app, "/c/ev-hb-reset/events") as conn:
        await _wait_for_client(stream)
        # Deliver data promptly; reading its frames also consumes them from the
        # native producer, which resets the keepalive timer at that instant.
        stream._dispatch_verdict(_make_verdict(contest.id))
        data = [_parse_event(f) for f in await conn.read_events(2)]
        assert [e["event"] for e in data] == ["verdict", "scoreboard_refresh"]

        # Within less than one interval after the data reset, no heartbeat fires —
        # proving the timer restarted rather than running free.
        quiet = await conn.read_within(interval * 0.5)
        assert quiet == []

        # After a full idle interval a heartbeat does arrive.
        beat = [_parse_event(f) for f in await conn.read_events(1, timeout=interval * 3)]
        assert beat[0].get("comment") == "ping"
