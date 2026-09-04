#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Concurrent-connection caps on the two Web SSE streams.

``/c/{slug}/live/events`` and ``/c/{slug}/runs/events`` share the ``web:sse``
per-IP lease; the runs stream also holds a per-actor slot. Both routes release
their request session before streaming.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI

from shared.enumerations import RoleEnum
from tests.animator._asgi_stream import ASGIStream
from tests.shared._sse_fake_valkey import SseFakeValkey
from web.database import get_db
from web.dependencies import ContestContext, get_contest_context
from web.routes.contest_live_feed import router as live_router
from web.routes.contest_runs_events import router as runs_router
from web.services import sse_limits
from web.services.contest_service import get_contest_by_slug

pytestmark = pytest.mark.asyncio

IP_A = ("203.0.113.10", 40000)
IP_B = ("198.51.100.7", 40000)


class _FakeRuntime(SseFakeValkey):
    async def iter_verdict_events(self) -> AsyncGenerator[Any]:
        await asyncio.Event().wait()
        yield None  # pragma: no cover


class _FakeSession:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _build_app(runtime: _FakeRuntime, *, actor_id: str = "actor-1") -> tuple[FastAPI, list[_FakeSession]]:
    app = FastAPI()
    app.state.valkey_runtime = runtime
    contest = SimpleNamespace(id="contest-1", login_slug="demo", upcoming=False, is_frozen=False)
    actor = SimpleNamespace(id=actor_id, role=RoleEnum.TEAM)
    sessions: list[_FakeSession] = []

    async def _db() -> AsyncIterator[_FakeSession]:
        fake = _FakeSession()
        sessions.append(fake)
        yield fake

    async def _ctx(session: Any = None) -> ContestContext:
        fake = _FakeSession()
        sessions.append(fake)
        return ContestContext(contest=contest, session=fake, actor=actor)  # type: ignore[arg-type]

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_contest_by_slug] = lambda: contest
    app.dependency_overrides[get_contest_context] = _ctx
    app.include_router(live_router)
    app.include_router(runs_router)
    return app, sessions


def _set_caps(monkeypatch: pytest.MonkeyPatch, max_per_ip: int, max_per_user: int) -> None:
    monkeypatch.setattr(sse_limits.settings, "SSE_LIMIT_ENABLED", True)
    monkeypatch.setattr(sse_limits.settings, "SSE_MAX_PER_IP", max_per_ip)
    monkeypatch.setattr(sse_limits.settings, "SSE_MAX_PER_USER", max_per_user)
    monkeypatch.setattr(sse_limits.settings, "SSE_CONNECTION_TTL_SECONDS", 600)
    monkeypatch.setattr(sse_limits.settings, "SSE_TRUSTED_CIDRS", "127.0.0.0/8")


async def test_both_streams_share_one_per_ip_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_caps(monkeypatch, 2, 10)
    runtime = _FakeRuntime()
    app, _ = _build_app(runtime)
    key = "noca:sse:web:sse:ip:203.0.113.10"

    async with ASGIStream(app, "/c/demo/live/events", client=IP_A) as first:
        assert first.status == 200
        async with ASGIStream(app, "/c/demo/runs/events", client=IP_A) as second:
            assert second.status == 200
            assert runtime.counts[key] == 2
            async with ASGIStream(app, "/c/demo/live/events", client=IP_A) as third:
                assert third.status == 429
            async with ASGIStream(app, "/c/demo/live/events", client=IP_B) as other:
                assert other.status == 200
        assert runtime.counts[key] == 1
    assert key not in runtime.counts


async def test_runs_stream_caps_per_actor_across_ips(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_caps(monkeypatch, 10, 1)
    runtime = _FakeRuntime()
    app, _ = _build_app(runtime, actor_id="judge-7")

    async with ASGIStream(app, "/c/demo/runs/events", client=IP_A) as first:
        assert first.status == 200
        assert runtime.counts["noca:sse:web:sse:user:judge-7"] == 1
        async with ASGIStream(app, "/c/demo/runs/events", client=IP_B) as second:
            assert second.status == 429
        # The refused attempt did not leak IP_B's slot.
        assert "noca:sse:web:sse:ip:198.51.100.7" not in runtime.counts
    assert runtime.counts == {}


async def test_streams_release_their_request_session_before_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_caps(monkeypatch, 10, 10)
    app, sessions = _build_app(_FakeRuntime())

    async with ASGIStream(app, "/c/demo/live/events", client=IP_A):
        pass
    async with ASGIStream(app, "/c/demo/runs/events", client=IP_A):
        pass
    assert sessions and all(fake.closed for fake in sessions)
