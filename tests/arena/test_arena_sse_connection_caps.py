#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Concurrent-connection caps on the two Arena SSE streams.

``/live/events`` and ``/user/submissions/status/events`` share the
``arena:sse`` per-IP lease and, for a logged-in user, one per-user slot keyed on
the stable ``ArenaUser.id``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from arena.dependencies import sse_limits
from arena.dependencies.auth import get_streaming_arena_user
from arena.models.arena_users import ArenaUser
from arena.routes.live import router as live_router
from arena.routes.user_submission_status import router as status_router
from tests.animator._asgi_stream import ASGIStream
from tests.arena._admin_problem_app import create_user
from tests.shared._sse_fake_valkey import SseFakeValkey

pytestmark = pytest.mark.asyncio

IP_A = ("203.0.113.10", 40000)
IP_B = ("198.51.100.7", 40000)


class _FakeRuntime(SseFakeValkey):
    async def iter_arena_verdict_events(self) -> AsyncGenerator[Any]:
        await asyncio.Event().wait()
        yield None  # pragma: no cover


def _build_app(session: AsyncSession, user: ArenaUser | None, runtime: _FakeRuntime) -> FastAPI:
    app = FastAPI()
    app.state.valkey_runtime = runtime
    app.state.arena_db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.dependency_overrides[get_streaming_arena_user] = lambda: user
    app.include_router(live_router)
    app.include_router(status_router)
    return app


def _set_caps(monkeypatch: pytest.MonkeyPatch, max_per_ip: int, max_per_user: int) -> None:
    monkeypatch.setattr(sse_limits.settings, "SSE_LIMIT_ENABLED", True)
    monkeypatch.setattr(sse_limits.settings, "SSE_MAX_PER_IP", max_per_ip)
    monkeypatch.setattr(sse_limits.settings, "SSE_MAX_PER_USER", max_per_user)
    monkeypatch.setattr(sse_limits.settings, "SSE_CONNECTION_TTL_SECONDS", 600)
    monkeypatch.setattr(sse_limits.settings, "SSE_TRUSTED_CIDRS", "127.0.0.0/8")


async def test_both_streams_share_one_per_ip_lease(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    user = await create_user(session, email="sse-ip@test.example")
    _set_caps(monkeypatch, 2, 10)
    runtime = _FakeRuntime()
    app = _build_app(session, user, runtime)
    key = "noca:sse:arena:sse:ip:203.0.113.10"

    async with ASGIStream(app, "/live/events", client=IP_A) as first:
        assert first.status == 200
        async with ASGIStream(app, "/user/submissions/status/events", client=IP_A) as second:
            assert second.status == 200
            assert runtime.counts[key] == 2
            async with ASGIStream(app, "/live/events", client=IP_A) as third:
                assert third.status == 429
            async with ASGIStream(app, "/live/events", client=IP_B) as other:
                assert other.status == 200
        assert runtime.counts[key] == 1
    assert key not in runtime.counts


async def test_per_user_cap_spans_ips_and_both_streams(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    user = await create_user(session, email="sse-user@test.example")
    _set_caps(monkeypatch, 10, 1)
    runtime = _FakeRuntime()
    app = _build_app(session, user, runtime)
    user_key = f"noca:sse:arena:sse:user:{user.id}"

    async with ASGIStream(app, "/live/events", client=IP_A) as first:
        assert first.status == 200
        assert runtime.counts[user_key] == 1
        async with ASGIStream(app, "/user/submissions/status/events", client=IP_B) as second:
            assert second.status == 429
        assert "noca:sse:arena:sse:ip:198.51.100.7" not in runtime.counts
    assert runtime.counts == {}


async def test_anonymous_stream_holds_only_the_ip_slot(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_caps(monkeypatch, 10, 1)
    runtime = _FakeRuntime()
    app = _build_app(session, None, runtime)

    async with (
        ASGIStream(app, "/live/events", client=IP_A) as first,
        ASGIStream(app, "/live/events", client=IP_B) as second,
    ):
        assert first.status == 200
        assert second.status == 200
        assert set(runtime.counts) == {"noca:sse:arena:sse:ip:203.0.113.10", "noca:sse:arena:sse:ip:198.51.100.7"}
    assert runtime.counts == {}
