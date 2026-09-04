#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Concurrent-connection caps on the two animator SSE streams.

Both ``/c/{slug}/events`` and ``/c/{slug}/reveal/events`` share the
``animator:sse`` per-IP lease and the process-wide ``MAX_SSE_CLIENTS`` gauge;
both refusals are decided before the contest gate and both slots are released
when the client disconnects.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from animator import dependencies
from animator.error_handlers import register_error_handlers
from animator.routes.public import router as public_router
from animator.routes.reveal_public import router as reveal_public_router
from animator.services.event_stream_service import AnimatorEventStream
from animator.services.feed_cache import AnimatorFeedCache
from animator.services.sse_capacity import SseCapacity
from tests.animator._asgi_stream import ASGIStream
from tests.animator._feed_seed import make_contest
from tests.shared._sse_fake_valkey import SseFakeValkey
from web.models.users import UberAdmin

pytestmark = pytest.mark.asyncio

IP_A = ("203.0.113.10", 40000)
IP_B = ("198.51.100.7", 40000)


class _FakeRuntime(SseFakeValkey):
    """The SSE-lease fake plus never-yielding pub/sub sources for both streams."""

    async def iter_verdict_events(self) -> AsyncGenerator[Any]:
        await asyncio.Event().wait()
        yield None  # pragma: no cover

    async def iter_submission_events(self) -> AsyncGenerator[Any]:
        await asyncio.Event().wait()
        yield None  # pragma: no cover

    async def iter_revelation_events(
        self, contest_id: str, scope: str, *, on_subscribed: Callable[[], None] | None = None
    ) -> AsyncGenerator[Any]:
        if on_subscribed is not None:
            on_subscribed()
        await asyncio.Event().wait()
        yield None  # pragma: no cover


def _build_app(engine: AsyncEngine, runtime: _FakeRuntime, *, max_clients: int = 2000) -> FastAPI:
    app = FastAPI()
    app.state.feed_cache = AnimatorFeedCache()
    app.state.db_session = async_sessionmaker(engine, expire_on_commit=False)
    app.state.valkey_runtime = runtime
    app.state.event_stream = AnimatorEventStream(runtime)  # unstarted: register() needs no tasks
    app.state.sse_capacity = SseCapacity(max_clients)
    app.include_router(public_router)
    app.include_router(reveal_public_router)
    register_error_handlers(app)
    return app


def _set_caps(monkeypatch: pytest.MonkeyPatch, max_per_ip: int, *, enabled: bool = True) -> None:
    monkeypatch.setattr(dependencies.settings, "SSE_LIMIT_ENABLED", enabled)
    monkeypatch.setattr(dependencies.settings, "SSE_MAX_PER_IP", max_per_ip)
    monkeypatch.setattr(dependencies.settings, "SSE_CONNECTION_TTL_SECONDS", 600)
    monkeypatch.setattr(dependencies.settings, "SSE_TRUSTED_CIDRS", "127.0.0.0/8")


async def _seed(session: AsyncSession, uberadmin: UberAdmin, slug: str) -> str:
    await make_contest(session, uberadmin, slug=slug)
    await session.commit()
    return slug


async def test_both_streams_share_one_per_ip_lease(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = await _seed(session, uberadmin, "sse-cap-shared")
    _set_caps(monkeypatch, 2)
    runtime = _FakeRuntime()
    app = _build_app(session.bind, runtime)  # type: ignore[arg-type]
    key = "noca:sse:animator:sse:ip:203.0.113.10"

    async with ASGIStream(app, f"/c/{slug}/events", client=IP_A) as first:
        assert first.status == 200
        async with ASGIStream(app, f"/c/{slug}/reveal/events", query_string="scope=global", client=IP_A) as second:
            assert second.status == 200
            assert runtime.counts[key] == 2
            async with ASGIStream(app, f"/c/{slug}/events", client=IP_A) as third:
                assert third.status == 429
            async with ASGIStream(app, f"/c/{slug}/events", client=IP_B) as other_ip:
                assert other_ip.status == 200
        # Closing one stream frees its slot.
        assert runtime.counts[key] == 1
        async with ASGIStream(app, f"/c/{slug}/reveal/events", query_string="scope=global", client=IP_A) as again:
            assert again.status == 200
    assert key not in runtime.counts


async def test_cap_is_decided_before_the_contest_gate(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = await _seed(session, uberadmin, "sse-cap-gate")
    _set_caps(monkeypatch, 1)
    app = _build_app(session.bind, _FakeRuntime())  # type: ignore[arg-type]

    async with (
        ASGIStream(app, f"/c/{slug}/events", client=IP_A),
        ASGIStream(app, "/c/no-such-contest/events", client=IP_A) as unknown,
    ):
        # Over budget, an unknown slug is refused by the cap, not by the 404 gate.
        assert unknown.status == 429
    async with ASGIStream(app, "/c/no-such-contest/events", client=IP_A) as unknown_under:
        assert unknown_under.status == 404


async def test_process_ceiling_covers_both_streams_and_answers_503(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = await _seed(session, uberadmin, "sse-cap-ceiling")
    _set_caps(monkeypatch, 10)
    app = _build_app(session.bind, _FakeRuntime(), max_clients=1)  # type: ignore[arg-type]
    capacity: SseCapacity = app.state.sse_capacity

    async with ASGIStream(app, f"/c/{slug}/events", client=IP_A) as first:
        assert first.status == 200
        assert capacity.active == 1
        async with ASGIStream(app, f"/c/{slug}/reveal/events", query_string="scope=global", client=IP_B) as full:
            assert full.status == 503
        assert capacity.active == 1
    assert capacity.active == 0


async def test_trusted_network_and_disabled_flag_bypass_the_lease(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = await _seed(session, uberadmin, "sse-cap-bypass")
    _set_caps(monkeypatch, 1)
    runtime = _FakeRuntime()
    app = _build_app(session.bind, runtime)  # type: ignore[arg-type]

    loopback = ("127.0.0.1", 40000)
    async with (
        ASGIStream(app, f"/c/{slug}/events", client=loopback),
        ASGIStream(app, f"/c/{slug}/events", client=loopback) as second,
    ):
        assert second.status == 200
    assert runtime.counts == {}

    _set_caps(monkeypatch, 1, enabled=False)
    async with (
        ASGIStream(app, f"/c/{slug}/events", client=IP_A),
        ASGIStream(app, f"/c/{slug}/events", client=IP_A) as s2,
    ):
        assert s2.status == 200
    assert runtime.counts == {}
