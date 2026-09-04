#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the Arena problem statistics page and its JSON payload."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_users import ArenaUser
from arena.services.problem_stats_service import UNKNOWN_SOLVER_NAME
from shared.db_schema.arena import arena_problem_statistics
from shared.enumerations import ArenaRole
from tests.arena.test_arena_problem_detail_routes import (
    _build_problem_detail_app,
    _create_enabled_problem,
    _create_user,
    _login_token,
)


def _build_app(session: AsyncSession) -> FastAPI:
    app = _build_problem_detail_app(session)

    @app.get("/profile/{user_id}", name="arena_user_profile_public")
    async def _public_profile(user_id: str) -> Response:
        return Response(f"profile {user_id}")

    return app


async def _make_solver(session: AsyncSession, *, email: str, public: bool) -> ArenaUser:
    user = await _create_user(session, name=f"Solver {email}", email=email, role=ArenaRole.ARENA_USER)
    user.public_profile = public
    user.ranking_visible = public
    await session.commit()
    return user


def _solver_payload(user: ArenaUser, solved_at: str) -> dict[str, Any]:
    return {"user_id": user.id, "name": user.nome, "solved_at": solved_at}


async def _store_snapshot(session: AsyncSession, problem_id: str, data: dict[str, Any]) -> None:
    await session.execute(
        insert(arena_problem_statistics).values(
            problem_id=problem_id,
            data=data,
            computed_at=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        )
    )
    await session.commit()


async def _get(app: FastAPI, user: ArenaUser, path: str) -> Any:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _login_token(app, user)},
    ) as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_statistics_page_renders_solver_surfaces(session: AsyncSession) -> None:
    """The page carries the new containers and loads the heatmap and solver-stats scripts."""
    app = _build_app(session)
    author = await _create_user(session, name="Author", email="stats-author@test.example", role=ArenaRole.ARENA_JUDGE)
    problem = await _create_enabled_problem(session, author)

    response = await _get(app, author, f"/problems/{problem.arena_number}/statistics")

    assert response.status_code == 200
    html = response.text
    for marker in (
        'id="problem-stats-attempts"',
        'id="problem-stats-heatmap"',
        "data-stats-heatmap-years",
        "data-stats-first-solver",
        "data-stats-last-solver",
        "data-stats-attempts-status",
        "arena-submission-heatmap.js",
        "arena-problem-solver-stats.js",
        "arena-problem-statistics.js",
    ):
        assert marker in html, marker
    # The problem page owns its heatmap payload; the auto-fetching binding must not claim it.
    assert "data-arena-submission-heatmap" not in html


@pytest.mark.asyncio
async def test_statistics_json_is_empty_without_snapshot(session: AsyncSession) -> None:
    """A problem with no computed snapshot answers ``{}``."""
    app = _build_app(session)
    author = await _create_user(session, name="Author", email="stats-empty@test.example", role=ArenaRole.ARENA_JUDGE)
    problem = await _create_enabled_problem(session, author)

    response = await _get(app, author, f"/problems/{problem.arena_number}/statistics.json")

    assert response.status_code == 200
    assert response.json() == {}


@pytest.mark.asyncio
async def test_statistics_json_links_only_viewable_solvers(session: AsyncSession) -> None:
    """Display timestamps land on both solvers; the profile link only on the public one."""
    app = _build_app(session)
    author = await _create_user(session, name="Author", email="stats-link@test.example", role=ArenaRole.ARENA_JUDGE)
    viewer = await _create_user(session, name="Viewer", email="stats-viewer@test.example", role=ArenaRole.ARENA_USER)
    public_solver = await _make_solver(session, email="public-solver@test.example", public=True)
    private_solver = await _make_solver(session, email="private-solver@test.example", public=False)
    problem = await _create_enabled_problem(session, author)
    await _store_snapshot(
        session,
        problem.id,
        {
            "total_submissions": 2,
            "first_solver": _solver_payload(public_solver, "2026-01-01T10:00:00+00:00"),
            "last_solver": _solver_payload(private_solver, "2026-02-01T10:00:00+00:00"),
        },
    )

    response = await _get(app, viewer, f"/problems/{problem.arena_number}/statistics.json")

    assert response.status_code == 200
    payload = response.json()
    assert payload["computed_at_display"]
    first, last = payload["first_solver"], payload["last_solver"]
    assert first["solved_at_display"] and last["solved_at_display"]
    assert first["profile_url"].endswith(f"/profile/{public_solver.id}")
    assert "profile_url" not in last
    # The snapshot froze ``nome``; the response re-resolves it through the age
    # shield, so an adult who never opted in is named by their handle.
    assert last["name"] == private_solver.username
    assert last["name"] != private_solver.nome


@pytest.mark.asyncio
async def test_statistics_json_admin_viewer_links_private_solver(session: AsyncSession) -> None:
    """An admin viewer may open any profile, so the private solver is linked too."""
    app = _build_app(session)
    admin = await _create_user(session, name="Admin", email="stats-admin@test.example", role=ArenaRole.ARENA_ADMIN)
    private_solver = await _make_solver(session, email="private-solver-2@test.example", public=False)
    problem = await _create_enabled_problem(session, admin)
    await _store_snapshot(
        session,
        problem.id,
        {
            "total_submissions": 1,
            "first_solver": _solver_payload(private_solver, "2026-01-01T10:00:00+00:00"),
            "last_solver": None,
        },
    )

    response = await _get(app, admin, f"/problems/{problem.arena_number}/statistics.json")

    payload = response.json()
    assert payload["first_solver"]["profile_url"].endswith(f"/profile/{private_solver.id}")
    assert payload["last_solver"] is None


@pytest.mark.asyncio
async def test_statistics_json_missing_solver_account_is_withheld_not_named(session: AsyncSession) -> None:
    """A solver whose account is gone loses its name as well as its link.

    Fail-closed: with no row there is no date of birth, so there is no way to
    know whether publishing the snapshot's frozen legal name is allowed.
    """
    app = _build_app(session)
    author = await _create_user(session, name="Author", email="stats-gone@test.example", role=ArenaRole.ARENA_JUDGE)
    problem = await _create_enabled_problem(session, author)
    await _store_snapshot(
        session,
        problem.id,
        {
            "total_submissions": 1,
            "first_solver": {"user_id": "gone-user", "name": "Ghost", "solved_at": "2026-01-01T10:00:00+00:00"},
            "last_solver": {"user_id": "gone-user", "name": "Ghost", "solved_at": "2026-01-01T10:00:00+00:00"},
        },
    )

    response = await _get(app, author, f"/problems/{problem.arena_number}/statistics.json")

    payload = response.json()
    assert payload["first_solver"]["name"] == UNKNOWN_SOLVER_NAME
    assert "Ghost" not in response.text
    assert "profile_url" not in payload["first_solver"]
    assert payload["first_solver"]["solved_at_display"]


@pytest.mark.asyncio
async def test_statistics_json_never_names_a_shielded_minor_solver(session: AsyncSession) -> None:
    """A 13-17 year-old first solver is named by their handle, not their legal name.

    The snapshot the rating worker writes carries ``nome``, so this is the one
    public surface where the shield has to override stored data rather than
    simply choose a column.
    """
    app = _build_app(session)
    author = await _create_user(session, name="Author", email="stats-minor@test.example", role=ArenaRole.ARENA_JUDGE)
    viewer = await _create_user(session, name="Viewer", email="stats-minor-v@test.example", role=ArenaRole.ARENA_USER)
    minor = await _make_solver(session, email="minor-solver@test.example", public=True)
    minor.nome = "Joana Menorista"
    minor.dta_nascimento = date(2010, 4, 2)
    minor.full_name_public = True
    await session.commit()
    problem = await _create_enabled_problem(session, author)
    await _store_snapshot(
        session,
        problem.id,
        {
            "total_submissions": 1,
            "first_solver": _solver_payload(minor, "2026-01-01T10:00:00+00:00"),
            "last_solver": None,
        },
    )

    response = await _get(app, viewer, f"/problems/{problem.arena_number}/statistics.json")

    payload = response.json()
    assert payload["first_solver"]["name"] == minor.username
    assert "Menorista" not in response.text
    # The shield closes the profile too, so no link may be offered.
    assert "profile_url" not in payload["first_solver"]


@pytest.mark.asyncio
async def test_statistics_json_keeps_an_adult_opt_in_name_and_link(session: AsyncSession) -> None:
    """An adult who opted in keeps both their legal name and their profile link."""
    app = _build_app(session)
    author = await _create_user(session, name="Author", email="stats-adult@test.example", role=ArenaRole.ARENA_JUDGE)
    viewer = await _create_user(session, name="Viewer", email="stats-adult-v@test.example", role=ArenaRole.ARENA_USER)
    adult = await _make_solver(session, email="adult-solver@test.example", public=True)
    adult.nome = "Adalberto Maiorista"
    adult.dta_nascimento = date(1990, 4, 2)
    adult.full_name_public = True
    await session.commit()
    problem = await _create_enabled_problem(session, author)
    await _store_snapshot(
        session,
        problem.id,
        {
            "total_submissions": 1,
            "first_solver": _solver_payload(adult, "2026-01-01T10:00:00+00:00"),
            "last_solver": None,
        },
    )

    response = await _get(app, viewer, f"/problems/{problem.arena_number}/statistics.json")

    payload = response.json()
    assert payload["first_solver"]["name"] == "Adalberto Maiorista"
    assert payload["first_solver"]["profile_url"].endswith(f"/profile/{adult.id}")
