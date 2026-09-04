#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Per-IP limits on Web's anonymous reads, and the production cache guard.

``GET /problem-set/{slug}.zip`` and ``GET /c/{slug}/live/feed.json`` each carry
their own fixed window, checked before the contest gate; the download also
refuses to rebuild archives per request in production.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.enumerations import Environment
from web.database import get_db
from web.models.contest import Contest
from web.routes.contest_live_feed import router as live_router
from web.routes.problem_set import UNCACHED_IN_PRODUCTION_DETAIL
from web.routes.problem_set import router as problem_set_router
from web.services import public_rate_limits
from web.services.public_rate_limits import (
    LIVE_FEED_DETAIL,
    PROBLEM_SET_DETAIL,
    enforce_live_feed_rate_limit,
    enforce_problem_set_rate_limit,
)
from web.services.sse_limits import enforce_live_events_slots

pytestmark = pytest.mark.asyncio

IP_A = "203.0.113.10"
IP_B = "198.51.100.7"


def _build_app(session: AsyncSession) -> FastAPI:
    app = FastAPI()
    app.state.db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.dependency_overrides[get_db] = lambda: session
    app.include_router(problem_set_router)
    app.include_router(live_router)
    return app


def _client(app: FastAPI, ip: str = IP_A) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 12345)), base_url="http://testserver")


def _set_limits(
    monkeypatch: pytest.MonkeyPatch, *, problem_set: int = 10, live_feed: int = 120, enabled: bool = True
) -> None:
    s = public_rate_limits.settings
    monkeypatch.setattr(s, "PUBLIC_RATE_LIMIT_ENABLED", enabled)
    monkeypatch.setattr(s, "PUBLIC_RATE_LIMIT_TRUSTED_CIDRS", "127.0.0.0/8")
    monkeypatch.setattr(s, "PUBLIC_RATE_LIMIT_PROBLEM_SET_MAX_REQUESTS", problem_set)
    monkeypatch.setattr(s, "PUBLIC_RATE_LIMIT_PROBLEM_SET_WINDOW_SECONDS", 600)
    monkeypatch.setattr(s, "PUBLIC_RATE_LIMIT_LIVE_FEED_MAX_REQUESTS", live_feed)
    monkeypatch.setattr(s, "PUBLIC_RATE_LIMIT_LIVE_FEED_WINDOW_SECONDS", 60)


def _zip(slug: str) -> str:
    return f"/problem-set/{slug}.zip"


def _feed(contest: Contest) -> str:
    return f"/c/{contest.login_slug}/live/feed.json"


async def test_exactly_the_two_anonymous_reads_carry_the_request_limits() -> None:
    """The SSE sibling keeps its connection cap and is outside both request buckets."""
    guarded: dict[str, set[str]] = {"problem-set": set(), "live-feed": set(), "sse": set()}
    for router in (problem_set_router, live_router):
        for route in router.routes:
            assert isinstance(route, APIRoute)
            deps = {dep.dependency for dep in route.dependencies}
            if enforce_problem_set_rate_limit in deps:
                guarded["problem-set"].add(route.path)
            if enforce_live_feed_rate_limit in deps:
                guarded["live-feed"].add(route.path)
            if enforce_live_events_slots in deps:
                guarded["sse"].add(route.path)
    assert guarded == {
        "problem-set": {"/problem-set/{slug}.zip"},
        "live-feed": {"/c/{slug}/live/feed.json"},
        "sse": {"/c/{slug}/live/events"},
    }


async def test_problem_set_window_answers_429_before_the_gate(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_limits(monkeypatch, problem_set=2)
    app = _build_app(session)

    async with _client(app) as client:
        first = await client.get(_zip("no-such-contest"))
        second = await client.get(_zip("no-such-contest"))
        third = await client.get(_zip("no-such-contest"))
    async with _client(app, IP_B) as other:
        unaffected = await other.get(_zip("no-such-contest"))

    assert (first.status_code, second.status_code) == (404, 404)
    # Over budget, the limiter answers before the gate could 404 the unknown slug.
    assert third.status_code == 429
    assert third.json() == {"detail": PROBLEM_SET_DETAIL}
    assert int(third.headers["Retry-After"]) >= 1
    assert unaffected.status_code == 404


async def test_live_feed_window_answers_429_with_retry_after(
    session: AsyncSession, running_contest: Contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_limits(monkeypatch, live_feed=2)
    app = _build_app(session)

    async with _client(app) as client:
        first = await client.get(_feed(running_contest))
        second = await client.get(_feed(running_contest))
        third = await client.get(_feed(running_contest))

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.headers["Cache-Control"] == "public, max-age=5"
    assert third.status_code == 429
    assert third.json() == {"detail": LIVE_FEED_DETAIL}
    assert "Retry-After" in third.headers


async def test_the_two_buckets_are_independent(
    session: AsyncSession, running_contest: Contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_limits(monkeypatch, problem_set=1, live_feed=1)
    app = _build_app(session)

    async with _client(app) as client:
        await client.get(_zip("no-such-contest"))
        spent_zip = await client.get(_zip("no-such-contest"))
        feed_still_open = await client.get(_feed(running_contest))
        spent_feed = await client.get(_feed(running_contest))

    assert spent_zip.status_code == 429
    assert feed_still_open.status_code == 200
    assert spent_feed.status_code == 429


async def test_trusted_networks_and_the_shared_switch_bypass_both(
    session: AsyncSession, running_contest: Contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_limits(monkeypatch, problem_set=1, live_feed=1)
    app = _build_app(session)

    async with _client(app, "127.0.0.1") as loopback:
        for _ in range(3):
            assert (await loopback.get(_zip("no-such-contest"))).status_code == 404
            assert (await loopback.get(_feed(running_contest))).status_code == 200

    _set_limits(monkeypatch, problem_set=1, live_feed=1, enabled=False)
    async with _client(app) as client:
        for _ in range(3):
            assert (await client.get(_zip("no-such-contest"))).status_code == 404
            assert (await client.get(_feed(running_contest))).status_code == 200


# ---------------------------------------------------------------------------
# Production refuses to rebuild archives per request
# ---------------------------------------------------------------------------


def _set_environment(monkeypatch: pytest.MonkeyPatch, environment: Environment, cache_dir: Path | None) -> None:
    from web.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", environment)
    monkeypatch.setattr(settings, "PUBLIC_PROBLEM_PACK_PATH", cache_dir)


async def test_production_without_a_cache_path_is_503_without_any_query(
    session: AsyncSession, stopped_contest: Contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_limits(monkeypatch)
    _set_environment(monkeypatch, Environment.PRODUCTION, None)
    stopped_contest.release_problem_set_after_end = True
    await session.commit()
    app = _build_app(session)

    statements: list[str] = []

    def _capture(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        statements.append(statement)

    engine = session.bind.sync_engine  # type: ignore[union-attr]
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        async with _client(app) as client:
            released = await client.get(_zip(stopped_contest.login_slug))
            unknown = await client.get(_zip("no-such-contest"))
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert released.status_code == 503
    assert released.json() == {"detail": UNCACHED_IN_PRODUCTION_DETAIL}
    # Every download is refused in this state, so nothing is disclosed and nothing is queried.
    assert unknown.status_code == 503
    assert statements == []


async def test_production_with_a_cache_path_still_serves(
    session: AsyncSession, stopped_contest: Contest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_limits(monkeypatch)
    _set_environment(monkeypatch, Environment.PRODUCTION, tmp_path)
    stopped_contest.release_problem_set_after_end = True
    await session.commit()
    app = _build_app(session)

    async with _client(app) as client:
        response = await client.get(_zip(stopped_contest.login_slug))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"


async def test_development_without_a_cache_path_keeps_building(
    session: AsyncSession, stopped_contest: Contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_limits(monkeypatch)
    _set_environment(monkeypatch, Environment.DEVELOPMENT, None)
    stopped_contest.release_problem_set_after_end = True
    await session.commit()
    app = _build_app(session)

    async with _client(app) as client:
        response = await client.get(_zip(stopped_contest.login_slug))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
