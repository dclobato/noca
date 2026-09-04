#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The loose per-actor ceiling on Web's polled partials and contest reads.

Two things have to hold at once, and they pull in opposite directions: a hostile
actor polling flat out must be stopped, and a contest full of browsers
refreshing the scoreboard every 30 seconds must never notice the limit exists.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.web.test_scoreboard_route import _build_app
from web.dependencies import ContestContext
from web.models.contest import Contest
from web.models.users import User
from web.routes import (
    contest_admin,
    contest_admin_problem_validator,
    contest_clarifications,
    contest_problems,
    contest_runs,
    contest_runs_events,
    contest_score,
    contest_solution_tests,
    contest_tasks,
)
from web.services import user_read_rate_limit
from web.services.sse_limits import enforce_runs_events_slots
from web.services.user_read_rate_limit import USER_READ_DETAIL, web_user_read_rate_limit

# Only the route tests are async; the two structural checks below run as-is.

IP_A = "203.0.113.10"

# Every router whose GET routes the audit named as polled or bounded reads.
_GUARDED_ROUTERS = (
    contest_runs.router,
    contest_tasks.router,
    contest_clarifications.router,
    contest_admin.router,
    contest_solution_tests.router,
    contest_admin_problem_validator.router,
    contest_problems.router,
    contest_score.router,
)


def _set_limit(monkeypatch: pytest.MonkeyPatch, *, max_requests: int = 300, enabled: bool = True) -> None:
    settings = user_read_rate_limit.settings
    monkeypatch.setattr(settings, "USER_READ_RATE_LIMIT_ENABLED", enabled)
    monkeypatch.setattr(settings, "USER_READ_RATE_LIMIT_MAX_REQUESTS", max_requests)
    monkeypatch.setattr(settings, "USER_READ_RATE_LIMIT_WINDOW_SECONDS", 60)


def _guards(route: APIRoute) -> set[object]:
    return {dependency.dependency for dependency in route.dependencies}


def test_every_polled_router_carries_the_ceiling() -> None:
    """Every GET added to a guarded router inherits the read ceiling."""
    for router in _GUARDED_ROUTERS:
        for route in router.routes:
            assert isinstance(route, APIRoute)
            if "GET" in route.methods:
                assert web_user_read_rate_limit in _guards(route), route.path


def test_the_sse_stream_is_excluded_but_its_json_sibling_is_not() -> None:
    """A stream is bounded by its connection lease; counting the request that opens it is meaningless."""
    guarded: set[str] = set()
    streamed: set[str] = set()
    for route in contest_runs_events.router.routes:
        assert isinstance(route, APIRoute)
        if web_user_read_rate_limit in _guards(route):
            guarded.add(route.path)
        if enforce_runs_events_slots in _guards(route):
            streamed.add(route.path)

    assert guarded == {"/c/{slug}/runs/{submission_id}/judging-history"}
    assert streamed == {"/c/{slug}/runs/events"}


@pytest.mark.asyncio
async def test_the_ceiling_answers_429_with_retry_after(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over budget, a polled partial is refused rather than served."""
    _set_limit(monkeypatch, max_requests=2)
    app = _build_app(ContestContext(contest=running_contest, session=session, actor=team_user))

    transport = ASGITransport(app=app, client=(IP_A, 12345))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        first = await client.get(f"/c/{running_contest.login_slug}/scoreboard/")
        second = await client.get(f"/c/{running_contest.login_slug}/scoreboard/")
        third = await client.get(f"/c/{running_contest.login_slug}/scoreboard/")

    assert (first.status_code, second.status_code) == (200, 200)
    assert third.status_code == 429
    assert third.json() == {"detail": USER_READ_DETAIL}
    assert int(third.headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_a_normal_poll_pattern_never_trips_the_default(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of a loose ceiling: an honest client must not feel it.

    The scoreboard refreshes every 30 s, so a browser spends 2 requests of the
    default 300 per minute. Even ten pages open at once, each refreshing on
    every one of the page's partials, stays an order of magnitude short.
    """
    _set_limit(monkeypatch)
    app = _build_app(ContestContext(contest=running_contest, session=session, actor=team_user))

    transport = ASGITransport(app=app, client=(IP_A, 12345))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for _ in range(40):
            assert (await client.get(f"/c/{running_contest.login_slug}/scoreboard/")).status_code == 200


@pytest.mark.asyncio
async def test_disabling_the_knob_removes_the_ceiling(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_limit(monkeypatch, max_requests=1, enabled=False)
    app = _build_app(ContestContext(contest=running_contest, session=session, actor=team_user))

    transport = ASGITransport(app=app, client=(IP_A, 12345))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for _ in range(4):
            assert (await client.get(f"/c/{running_contest.login_slug}/scoreboard/")).status_code == 200
