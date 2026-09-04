#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The loose per-user ceiling on Arena's authenticated reads and polled partials.

Two things have to hold at once, and they pull in opposite directions: one actor
polling flat out must be stopped, and the notification badge every page refreshes
must never notice the limit exists.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_users  # noqa: F401
from arena.database import get_db
from arena.dependencies import user_read_rate_limit
from arena.dependencies.auth import get_current_arena_user
from arena.dependencies.sse_limits import enforce_sse_connection_caps
from arena.dependencies.user_read_rate_limit import (
    USER_READ_DETAIL,
    arena_user_poll_rate_limit,
    arena_user_read_rate_limit,
)
from arena.models.arena_users import ArenaUser
from arena.routes import (
    admin_users,
    live,
    notifications,
    presence,
    problem_editorial,
    problems,
    ranking,
    submissions,
    user_public_profile,
    user_submission_status,
)
from shared.enumerations import ArenaRole

IP_A = "203.0.113.10"

# Routers whose GET surface contains authenticated reads or bounded partials.
_GUARDED_ROUTERS = (
    notifications.router,
    problems.router,
    problem_editorial.router,
    user_public_profile.router,
    ranking.router,
    submissions.router,
    admin_users.router,
)


def _set_limit(monkeypatch: pytest.MonkeyPatch, *, max_requests: int = 300, enabled: bool = True) -> None:
    settings = user_read_rate_limit.settings
    monkeypatch.setattr(settings, "USER_READ_RATE_LIMIT_ENABLED", enabled)
    monkeypatch.setattr(settings, "USER_READ_RATE_LIMIT_MAX_REQUESTS", max_requests)
    monkeypatch.setattr(settings, "USER_READ_RATE_LIMIT_WINDOW_SECONDS", 60)


def _guards(route: APIRoute) -> set[object]:
    return {dependency.dependency for dependency in route.dependencies}


def _paths_with_ceiling(router: object) -> set[str]:
    paths: set[str] = set()
    for route in router.routes:  # type: ignore[attr-defined]
        assert isinstance(route, APIRoute)
        if arena_user_read_rate_limit in _guards(route):
            paths.add(route.path)
    return paths


def test_every_polled_router_carries_the_ceiling() -> None:
    """Every GET added to a guarded router inherits the read ceiling."""
    for router in _GUARDED_ROUTERS:
        for route in router.routes:
            assert isinstance(route, APIRoute)
            if "GET" in route.methods:
                assert arena_user_read_rate_limit in _guards(route), route.path


def test_post_based_presence_polls_use_the_explicit_poll_ceiling() -> None:
    """Presence is the deliberate POST exception because both calls are timer-driven."""
    for route in presence.router.routes:
        assert isinstance(route, APIRoute)
        assert arena_user_poll_rate_limit in _guards(route), route.path


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        (live, {"/live", "/live/feed.json"}),
        (user_submission_status, {"/user/submissions/status.json"}),
    ],
)
def test_streams_are_excluded_but_their_json_siblings_are_not(module: object, expected: set[str]) -> None:
    """A stream holds one connection for minutes; the lease bounds it, not a per-minute count."""
    router = module.router  # type: ignore[attr-defined]
    streamed = {
        route.path
        for route in router.routes
        if isinstance(route, APIRoute) and enforce_sse_connection_caps in _guards(route)
    }

    assert _paths_with_ceiling(router) == expected
    assert streamed and not (streamed & expected)


async def _make_user(session: AsyncSession) -> ArenaUser:
    """Create and flush one active Arena user."""
    user = ArenaUser(
        nome="Read Ceiling User",
        email_normalizado=f"{uuid.uuid4().hex}@test.example",
        dta_nascimento=date(1995, 1, 1),
        role=ArenaRole.ARENA_USER,
    )
    user.password = "Senha@Forte1!"
    session.add(user)
    await session.flush()
    return user


def _build_app(session: AsyncSession, current_user: ArenaUser | None) -> FastAPI:
    """Build a minimal app carrying the notifications router and its ceiling."""
    app = FastAPI()
    app.include_router(notifications.router)

    async def _override_db():
        yield session

    async def _override_current_user():
        return current_user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_arena_user] = _override_current_user
    return app


def _client(app: FastAPI, *, ip: str = IP_A) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 12345)), base_url="http://testserver")


@pytest.mark.asyncio
async def test_the_ceiling_answers_429_with_retry_after(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Over budget, the polled notification partial is refused rather than served."""
    _set_limit(monkeypatch, max_requests=2)
    app = _build_app(session, await _make_user(session))

    async with _client(app) as client:
        first = await client.get("/arena/notifications")
        second = await client.get("/arena/notifications")
        third = await client.get("/arena/notifications")

    assert (first.status_code, second.status_code) == (200, 200)
    assert third.status_code == 429
    assert third.json() == {"detail": USER_READ_DETAIL}
    assert int(third.headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_a_normal_poll_pattern_never_trips_the_default(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of a loose ceiling: an honest client must not feel it.

    The badge refreshes on a timer measured in tens of seconds, so a browser
    spends a handful of the default 300 per minute. Several open tabs stay an
    order of magnitude short.
    """
    _set_limit(monkeypatch)
    app = _build_app(session, await _make_user(session))

    async with _client(app) as client:
        for _ in range(40):
            assert (await client.get("/arena/notifications")).status_code == 200


@pytest.mark.asyncio
async def test_disabling_the_knob_removes_the_ceiling(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_limit(monkeypatch, max_requests=1, enabled=False)
    app = _build_app(session, await _make_user(session))

    async with _client(app) as client:
        for _ in range(4):
            assert (await client.get("/arena/notifications")).status_code == 200
