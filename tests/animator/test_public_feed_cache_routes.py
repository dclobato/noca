#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the cached, rate-limited public feeds (``/meta``, ``/snapshot``).

The reveal spectator feed shares the same limiter and is covered together with
its dataset cache in ``test_reveal_dataset_cache.py``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from animator import dependencies
from animator.config import settings
from animator.dependencies import PUBLIC_RATE_LIMIT_DETAIL, enforce_public_rate_limit, enforce_sse_connection_caps
from animator.routes.public import router as public_router
from animator.routes.reveal_public import router as reveal_public_router
from animator.routes.team_media import router as team_media_router
from animator.services import contest_feed_service
from animator.services.feed_cache import AnimatorFeedCache
from tests.animator._feed_seed import make_contest, seed_dataset
from web.models.site import Site
from web.models.users import UberAdmin

pytestmark = pytest.mark.asyncio


def _build_app(engine: AsyncEngine) -> FastAPI:
    app = FastAPI()
    app.state.feed_cache = AnimatorFeedCache()
    app.state.db_session = async_sessionmaker(engine, expire_on_commit=False)
    app.include_router(public_router)
    return app


def _client(app: FastAPI, ip: str = "203.0.113.10") -> AsyncClient:
    """HTTP client presenting a routable client IP to the limiter."""
    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 12345)), base_url="http://test")


class _QueryCounter:
    """Counts SQL statements executed on the engine within a context."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._sync_engine = engine.sync_engine
        self.count = 0

    def _on_execute(self, *args: object) -> None:
        self.count += 1

    def __enter__(self) -> _QueryCounter:
        event.listen(self._sync_engine, "after_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc: object) -> None:
        event.remove(self._sync_engine, "after_cursor_execute", self._on_execute)


def _set_public_limit(monkeypatch: pytest.MonkeyPatch, max_requests: int, *, enabled: bool = True) -> None:
    monkeypatch.setattr(dependencies.settings, "PUBLIC_RATE_LIMIT_ENABLED", enabled)
    monkeypatch.setattr(dependencies.settings, "PUBLIC_RATE_LIMIT_MAX_REQUESTS", max_requests)
    monkeypatch.setattr(dependencies.settings, "PUBLIC_RATE_LIMIT_WINDOW_SECONDS", 60)
    monkeypatch.setattr(dependencies.settings, "PUBLIC_RATE_LIMIT_TRUSTED_CIDRS", "127.0.0.0/8")


async def _seed(session: AsyncSession, uberadmin: UberAdmin, slug: str, **kwargs: object) -> str:
    contest = await make_contest(session, uberadmin, slug=slug, **kwargs)  # type: ignore[arg-type]
    await seed_dataset(session, contest, uberadmin, teams=2, problems=2, submissions=2)
    await session.commit()
    return slug


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


async def test_snapshot_is_served_from_cache_byte_identical(session: AsyncSession, uberadmin: UberAdmin) -> None:
    running = await _seed(session, uberadmin, "cache-snap", start_time=datetime.now(UTC) - timedelta(minutes=10))
    ended = await _seed(session, uberadmin, "cache-snap-ended")
    engine: AsyncEngine = session.bind  # type: ignore[assignment]
    app = _build_app(engine)

    async with _client(app) as client:
        with _QueryCounter(engine) as first_queries:
            first = await client.get(f"/c/{running}/snapshot")
        with _QueryCounter(engine) as second_queries:
            second = await client.get(f"/c/{running}/snapshot")
        final = await client.get(f"/c/{ended}/snapshot")

    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert first.json()["version"] == second.json()["version"]
    assert first.headers["cache-control"] == f"public, max-age={settings.SNAPSHOT_CACHE_SECONDS}"
    assert final.headers["cache-control"] == f"public, max-age={settings.SNAPSHOT_CACHE_ENDED_SECONDS}"
    # The contest gate still runs per request (one query); the dataset load does not.
    assert first_queries.count > second_queries.count == 1


async def test_snapshot_scopes_are_separate_entries_and_invalidation_rebuilds(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    contest = await make_contest(session, uberadmin, slug="cache-scope")
    await seed_dataset(session, contest, uberadmin, teams=2, problems=2, submissions=2)
    await session.commit()
    site_id = (await session.execute(select(Site.id).where(Site.contest_id == contest.id))).scalar_one()
    engine: AsyncEngine = session.bind  # type: ignore[assignment]
    app = _build_app(engine)
    contest_id = str(contest.id)

    async with _client(app) as client:
        global_first = await client.get("/c/cache-scope/snapshot")
        with _QueryCounter(engine) as site_queries:
            site_first = await client.get(f"/c/cache-scope/snapshot?scope={site_id}")
        app.state.feed_cache.invalidate_contest(contest_id)
        with _QueryCounter(engine) as rebuilt_queries:
            global_again = await client.get("/c/cache-scope/snapshot")

    assert global_first.status_code == site_first.status_code == global_again.status_code == 200
    assert site_queries.count > 1, "a site scope is its own entry, built on first hit"
    assert rebuilt_queries.count > 1, "invalidation forces the next hit to reload"
    assert global_first.content != site_first.content


async def test_concurrent_snapshot_misses_build_once(session: AsyncSession, uberadmin: UberAdmin) -> None:
    slug = await _seed(session, uberadmin, "cache-flight")
    engine: AsyncEngine = session.bind  # type: ignore[assignment]
    app = _build_app(engine)

    async with _client(app) as client:
        with _QueryCounter(engine) as queries:
            responses = await asyncio.gather(*(client.get(f"/c/{slug}/snapshot") for _ in range(6)))

    assert {response.status_code for response in responses} == {200}
    assert len({response.content for response in responses}) == 1
    # Six gate queries plus one dataset load (teams + problems + submissions).
    assert queries.count == 6 + 3


async def test_pre_start_snapshot_entry_is_not_served_after_the_start(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = datetime.now(UTC) + timedelta(hours=1)
    slug = await _seed(session, uberadmin, "cache-start", start_time=start)
    app = _build_app(session.bind)  # type: ignore[arg-type]
    clock = {"now": start - timedelta(seconds=1)}
    monkeypatch.setattr(contest_feed_service, "_now_utc", lambda now: now if now is not None else clock["now"])

    async with _client(app) as client:
        before = await client.get(f"/c/{slug}/snapshot")
        clock["now"] = start + timedelta(seconds=1)
        after = await client.get(f"/c/{slug}/snapshot")

    assert before.json()["has_started"] is False
    assert before.json()["problems"] == []
    assert after.json()["has_started"] is True
    assert [problem for problem in after.json()["problems"]] == ["A", "B"]


async def test_meta_is_cached_and_shared_with_the_launcher(session: AsyncSession, uberadmin: UberAdmin) -> None:
    slug = await _seed(session, uberadmin, "cache-meta")
    engine: AsyncEngine = session.bind  # type: ignore[assignment]
    app = _build_app(engine)

    async with _client(app) as client:
        first = await client.get(f"/c/{slug}/meta")
        with _QueryCounter(engine) as queries:
            second = await client.get(f"/c/{slug}/meta")

    assert first.content == second.content
    assert first.headers["cache-control"] == "public, max-age=30"
    assert queries.count == 1


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


async def test_exactly_the_five_public_routes_are_rate_limited() -> None:
    """The HTML shells stay outside the bucket; the media routes are in.

    The two SSE streams carry a *different* guard -- the concurrent-connection
    cap -- rather than the request-rate bucket, and exactly those two do.
    """
    limited: set[str] = set()
    capped: set[str] = set()
    for router in (public_router, reveal_public_router, team_media_router):
        for route in router.routes:
            assert isinstance(route, APIRoute)
            if any(dep.dependency is enforce_public_rate_limit for dep in route.dependencies):
                limited.add(route.path)
            if any(dep.dependency is enforce_sse_connection_caps for dep in route.dependencies):
                capped.add(route.path)
    assert capped == {"/c/{slug}/events", "/c/{slug}/reveal/events"}
    assert limited == {
        "/c/{slug}/meta",
        "/c/{slug}/snapshot",
        "/c/{slug}/reveal/state",
        "/c/{slug}/teams/{team_id}/photo",
        "/c/{slug}/teams/{team_id}/audio",
    }


async def test_media_and_feeds_share_one_per_ip_window(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = await _seed(session, uberadmin, "limit-media")
    _set_public_limit(monkeypatch, 3)
    app = _build_app(session.bind)  # type: ignore[arg-type]
    app.include_router(team_media_router)

    async with _client(app) as client:
        assert (await client.get(f"/c/{slug}/meta")).status_code == 200
        assert (await client.get(f"/c/{slug}/teams/nobody/photo")).status_code == 404
        assert (await client.get(f"/c/{slug}/teams/nobody/audio")).status_code == 404
        rejected = await client.get(f"/c/{slug}/teams/nobody/photo")
        # An unknown slug is refused by the limiter before the gate could 404 it.
        unknown_over = await client.get("/c/no-such-contest/teams/nobody/photo")
    async with _client(app, ip="198.51.100.7") as other:
        unaffected = await other.get(f"/c/{slug}/teams/nobody/photo")

    assert rejected.status_code == 429
    assert rejected.json() == {"detail": PUBLIC_RATE_LIMIT_DETAIL}
    assert unknown_over.status_code == 429
    assert unaffected.status_code == 404


async def test_feeds_share_one_per_ip_window(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = await _seed(session, uberadmin, "limit-shared")
    _set_public_limit(monkeypatch, 3)
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        assert (await client.get(f"/c/{slug}/meta")).status_code == 200
        assert (await client.get(f"/c/{slug}/snapshot")).status_code == 200
        assert (await client.get(f"/c/{slug}/meta")).status_code == 200
        rejected = await client.get(f"/c/{slug}/snapshot")
    async with _client(app, ip="198.51.100.7") as other:
        unaffected = await other.get(f"/c/{slug}/meta")

    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"] == "60"
    assert rejected.json() == {"detail": PUBLIC_RATE_LIMIT_DETAIL}
    assert unaffected.status_code == 200


async def test_limit_runs_before_the_gate_and_never_names_the_slug(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under the limit, unknown and disabled contests are the same 404; over it, the same 429."""
    await _seed(session, uberadmin, "limit-disabled", animator_enabled=False)
    _set_public_limit(monkeypatch, 2)
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        unknown = await client.get("/c/no-such-contest/snapshot")
        disabled = await client.get("/c/limit-disabled/snapshot")
        unknown_over = await client.get("/c/no-such-contest/meta")
        disabled_over = await client.get("/c/limit-disabled/meta")

    assert unknown.status_code == disabled.status_code == 404
    assert unknown.content == disabled.content
    assert unknown_over.status_code == disabled_over.status_code == 429
    assert unknown_over.content == disabled_over.content


async def test_trusted_network_and_disable_switch(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = await _seed(session, uberadmin, "limit-trusted")
    _set_public_limit(monkeypatch, 1)
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app, ip="127.0.0.1") as trusted:
        statuses = [(await trusted.get(f"/c/{slug}/meta")).status_code for _ in range(3)]
    assert statuses == [200] * 3

    _set_public_limit(monkeypatch, 1, enabled=False)
    async with _client(app) as client:
        assert [(await client.get(f"/c/{slug}/snapshot")).status_code for _ in range(3)] == [200] * 3
