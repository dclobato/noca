#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The two per-problem downloads every contest actor -- teams included -- can reach.

Both used to do their full work on every request: the export rebuilt a package
into a tempfile, and the statement read its whole file into memory with no
validators at all. During a contest a team opens a statement repeatedly and can
loop the export, so what is asserted here is that repetition is cheap and that
making it cheap did not weaken the access gate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import Environment, RoleEnum
from shared.services.problem_export_cache import cached_export_path, export_cache_dir
from tests.conftest import _make_user
from tests.web.test_contest_problems_route import _build_app, _create_problem
from web.dependencies import ContestContext
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.users import UberAdmin, User
from web.routes.contest_problems import STATEMENT_CACHE_CONTROL, UNCACHED_IN_PRODUCTION_DETAIL
from web.services.problem_export_rate_limit import (
    PROBLEM_EXPORT_BUCKET,
    PROBLEM_EXPORT_DETAIL,
    web_problem_export_rate_limit,
)
from web.services.problem_service.files import save_md_statement

# Only the download tests are async; the two structural checks at the end are not.

_BUILD = "web.routes.contest_problems.build_problem_export"


def _client(app: FastAPI, *, ip: str = "203.0.113.10") -> AsyncClient:
    """Return a client for the built app, addressed from ``ip``."""
    return AsyncClient(
        transport=ASGITransport(app=app, client=(ip, 12345)),
        base_url="http://test",
        follow_redirects=False,
    )


def _app(session: AsyncSession, contest: Contest, actor: UberAdmin | User) -> FastAPI:
    """Build the problem-router app for one actor."""
    return _build_app(ContestContext(contest=contest, session=session, actor=actor))


def _url(problem: Problem, kind: str) -> str:
    """Return one download URL, addressed by display label."""
    return f"/c/{problem.contest_id}/problems/A/{kind}"


def _configure_cache(monkeypatch: pytest.MonkeyPatch, cache_root: Path | None) -> None:
    """Point Web's package cache at ``cache_root`` (or disable it)."""
    from web.config import settings

    monkeypatch.setattr(settings, "PUBLIC_PROBLEM_PACK_PATH", cache_root)
    monkeypatch.setattr(settings, "ENVIRONMENT", Environment.DEVELOPMENT)


def _set_export_budget(monkeypatch: pytest.MonkeyPatch, *, max_requests: int = 10, enabled: bool = True) -> None:
    """Resize the per-actor export budget for one test."""
    from web.services import problem_export_rate_limit

    monkeypatch.setattr(problem_export_rate_limit.settings, "PROBLEM_EXPORT_RATE_LIMIT_ENABLED", enabled)
    monkeypatch.setattr(problem_export_rate_limit.settings, "PROBLEM_EXPORT_RATE_LIMIT_MAX_REQUESTS", max_requests)
    monkeypatch.setattr(problem_export_rate_limit.settings, "PROBLEM_EXPORT_RATE_LIMIT_WINDOW_SECONDS", 600)


async def _problem_with_statement(session: AsyncSession, contest: Contest, *, title: str = "Two Sum") -> Problem:
    """Create a problem whose Markdown statement really exists on disk."""
    from web.config import settings

    problem = await _create_problem(session, contest)
    problem.title = title
    await session.commit()
    save_md_statement(problem.id, "# Statement\n", settings.PROBLEM_STATEMENT_DIR)
    return problem


# ── The statement download ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_statement_carries_validators_and_a_revalidating_directive(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """A positive `max-age` would let a browser skip the pre-start access gate."""
    problem = await _problem_with_statement(session, running_contest)

    async with _client(_app(session, running_contest, uberadmin)) as client:
        response = await client.get(_url(problem, "statement"))

    assert response.status_code == 200
    assert response.content == b"# Statement\n"
    assert response.headers["cache-control"] == STATEMENT_CACHE_CONTROL
    assert STATEMENT_CACHE_CONTROL == "private, no-cache"
    assert response.headers["etag"]
    assert response.headers["content-type"].startswith("text/markdown")


@pytest.mark.asyncio
async def test_a_matching_etag_answers_304_with_no_body(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """Reuse costs a header exchange instead of the whole file."""
    problem = await _problem_with_statement(session, running_contest)
    app = _app(session, running_contest, uberadmin)

    async with _client(app) as client:
        first = await client.get(_url(problem, "statement"))
        second = await client.get(_url(problem, "statement"), headers={"If-None-Match": first.headers["etag"]})

    assert first.status_code == 200
    assert second.status_code == 304
    assert second.content == b""
    # The directives have to survive the 304, or the next reuse is governed by
    # whatever the client inferred instead of by the gate-preserving policy.
    assert second.headers["cache-control"] == STATEMENT_CACHE_CONTROL
    # ...but a 304 carries no content, so it must not describe any.
    assert "content-disposition" not in second.headers


@pytest.mark.asyncio
async def test_the_access_gate_runs_ahead_of_every_conditional_request(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """A valid ETag does not get a team past the pre-start gate.

    This is the property `no-cache` exists to preserve: a browser must come back
    for every reuse, and when it does the gate is evaluated before anything is
    read from disk -- so a team cannot turn a cached statement into a `304`
    while the contest it belongs to has not started.
    """
    contest = Contest(
        contest_name="Upcoming Download Contest",
        contest_url="http://upcoming.example.com",
        login_slug="upcoming-downloads",
        start_time=datetime.now(UTC) + timedelta(hours=3),
        duration_minutes=120,
        stop_answers_after=120,
        stop_updating_scoreboard=120,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(contest)
    await session.flush()
    problem = await _problem_with_statement(session, contest)
    team = _make_user(session, contest, uberadmin, "team_dl", "Team DL", RoleEnum.TEAM)
    await session.commit()

    # A judge or admin may read a problem before the start; a team may not.
    async with _client(_app(session, contest, uberadmin)) as client:
        allowed = await client.get(_url(problem, "statement"))
        etag = allowed.headers["etag"]

    async with _client(_app(session, contest, team)) as client:
        refused = await client.get(_url(problem, "statement"), headers={"If-None-Match": etag})

    assert allowed.status_code == 200
    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_a_hostile_title_cannot_break_the_content_disposition_header(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """The filename is sanitized rather than interpolated raw."""
    problem = await _problem_with_statement(session, running_contest, title='Olá "quoted"; drop')

    async with _client(_app(session, running_contest, uberadmin)) as client:
        response = await client.get(_url(problem, "statement"))

    disposition = response.headers["content-disposition"]
    assert disposition == 'inline; filename="Ola_quoted_drop-statement.md"'
    assert '"' not in disposition[len("inline; filename=") + 1 : -1]


@pytest.mark.asyncio
async def test_a_problem_with_no_statement_file_is_404(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    problem = await _create_problem(session, running_contest)
    await session.commit()

    async with _client(_app(session, running_contest, uberadmin)) as client:
        response = await client.get(_url(problem, "statement"))

    assert response.status_code == 404


# ── The export download ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_second_export_is_served_from_cache_without_rebuilding(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The whole point: repetition costs a cache read, not a package build."""
    _configure_cache(monkeypatch, tmp_path)
    _set_export_budget(monkeypatch)
    problem = await _problem_with_statement(session, running_contest)
    app = _app(session, running_contest, uberadmin)

    with patch(_BUILD, side_effect=_fake_build) as build:
        async with _client(app) as client:
            first = await client.get(_url(problem, "export"))
            second = await client.get(_url(problem, "export"))

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.content == second.content
    assert build.call_count == 1
    assert cached_export_path(export_cache_dir(tmp_path), problem.id).is_file()


@pytest.mark.asyncio
async def test_editing_the_problem_invalidates_the_cached_export(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A bumped counter is what makes the next download rebuild."""
    _configure_cache(monkeypatch, tmp_path)
    _set_export_budget(monkeypatch)
    problem = await _problem_with_statement(session, running_contest)
    app = _app(session, running_contest, uberadmin)

    with patch(_BUILD, side_effect=_fake_build) as build:
        async with _client(app) as client:
            await client.get(_url(problem, "export"))
            await session.execute(
                update(Problem)
                .where(Problem.id == problem.id)
                .values(public_export_generation=Problem.public_export_generation + 1)
            )
            await session.commit()
            await client.get(_url(problem, "export"))

    assert build.call_count == 2


@pytest.mark.asyncio
async def test_the_export_budget_is_spent_even_on_cache_hits(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A cache hit is cheap but not free, and the budget must not be editable.

    The budget is charged before the cache is consulted, so a caller cannot widen
    it by arranging for hits -- nor is it spared by arranging for misses.
    """
    _configure_cache(monkeypatch, tmp_path)
    _set_export_budget(monkeypatch, max_requests=2)
    problem = await _problem_with_statement(session, running_contest)

    with patch(_BUILD, side_effect=_fake_build):
        async with _client(_app(session, running_contest, uberadmin)) as client:
            first = await client.get(_url(problem, "export"))
            second = await client.get(_url(problem, "export"))
            third = await client.get(_url(problem, "export"))
        # A different caller has a budget of its own; with no session cookie in
        # this harness the key falls back to the client address, which is what
        # keeps one caller's loop from refusing everyone else.
        async with _client(_app(session, running_contest, uberadmin), ip="203.0.113.99") as other:
            unaffected = await other.get(_url(problem, "export"))

    assert (first.status_code, second.status_code) == (200, 200)
    assert third.status_code == 429
    assert third.json() == {"detail": PROBLEM_EXPORT_DETAIL}
    assert int(third.headers["Retry-After"]) >= 1
    assert unaffected.status_code == 200


@pytest.mark.asyncio
async def test_the_budget_can_be_disabled(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_cache(monkeypatch, tmp_path)
    _set_export_budget(monkeypatch, max_requests=1, enabled=False)
    problem = await _problem_with_statement(session, running_contest)

    with patch(_BUILD, side_effect=_fake_build):
        async with _client(_app(session, running_contest, uberadmin)) as client:
            statuses = [(await client.get(_url(problem, "export"))).status_code for _ in range(3)]

    assert statuses == [200, 200, 200]


@pytest.mark.asyncio
async def test_without_a_cache_path_development_still_builds_per_request(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production refuses to start in this state; development keeps working."""
    _configure_cache(monkeypatch, None)
    _set_export_budget(monkeypatch)
    problem = await _problem_with_statement(session, running_contest)

    with patch(_BUILD, side_effect=_fake_build) as build:
        async with _client(_app(session, running_contest, uberadmin)) as client:
            first = await client.get(_url(problem, "export"))
            second = await client.get(_url(problem, "export"))

    assert (first.status_code, second.status_code) == (200, 200)
    assert build.call_count == 2


@pytest.mark.asyncio
async def test_production_without_a_cache_path_is_503_before_any_query(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every export is refused in that state, so nothing is looked up at all."""
    from web.config import settings

    _set_export_budget(monkeypatch)
    monkeypatch.setattr(settings, "PUBLIC_PROBLEM_PACK_PATH", None)
    monkeypatch.setattr(settings, "ENVIRONMENT", Environment.PRODUCTION)
    problem = await _problem_with_statement(session, running_contest)

    statements: list[str] = []

    def _capture(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        statements.append(statement)

    engine = session.bind.sync_engine  # type: ignore[union-attr]
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        async with _client(_app(session, running_contest, uberadmin)) as client:
            refused = await client.get(_url(problem, "export"))
            unknown = await client.get(f"/c/{running_contest.id}/problems/ZZ/export")
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert refused.status_code == 503
    assert refused.json() == {"detail": UNCACHED_IN_PRODUCTION_DETAIL}
    # An unknown label is refused identically, and neither reached the database.
    assert unknown.status_code == 503
    assert statements == []


# ── Structural: the guards are attached, not merely available ────────────────


def test_the_export_route_carries_its_own_budget_under_the_router_ceiling() -> None:
    """The tight budget is per route; the loose ceiling stays per router.

    Asserted structurally because both are `Depends` declarations that a later
    edit could silently drop, and neither failure is visible until a contest is
    already under load.
    """
    from web.routes import contest_problems
    from web.services.user_read_rate_limit import web_user_read_rate_limit

    router_guards = {dependency.dependency for dependency in contest_problems.router.dependencies}
    assert web_user_read_rate_limit in router_guards

    export_routes = [
        route
        for route in contest_problems.router.routes
        if isinstance(route, APIRoute) and route.name == "contest_problem_export"
    ]
    assert len(export_routes) == 1
    route_guards = {dependency.dependency for dependency in export_routes[0].dependencies}
    assert web_problem_export_rate_limit in route_guards
    # The statement download is bounded by the router ceiling alone: it is a file
    # read, not a package build, and revalidation makes repetition nearly free.
    statement_routes = [
        route
        for route in contest_problems.router.routes
        if isinstance(route, APIRoute) and route.name == "contest_problem_statement"
    ]
    assert len(statement_routes) == 1
    assert web_problem_export_rate_limit not in {d.dependency for d in statement_routes[0].dependencies}


def test_the_two_budgets_do_not_share_a_bucket() -> None:
    """Sharing one would let statement polling spend the export budget."""
    from web.services.user_read_rate_limit import USER_READ_BUCKET

    assert PROBLEM_EXPORT_BUCKET != USER_READ_BUCKET


def _fake_build(problem: Problem, _testcase_dir: Path, _statement_dir: Path, destination: Path, **_: object) -> Path:
    """Stand in for the real package builder, which needs a full problem tree."""
    destination.write_bytes(f"package:{problem.id}".encode())
    return destination
