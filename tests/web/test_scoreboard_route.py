#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route coverage for backend scoreboard site filtering."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from web.dependencies import ContestContext, get_contest_context
from web.models.contest import Contest
from web.models.site import Site
from web.models.users import UberAdmin, User
from web.routes import contest_score
from web.services.scoreboard import ScoreboardSnapshot, TeamStanding
from web.services.site_service import normalize_site_name_key
from web.template_globals import template_globals


def _build_app(ctx: ContestContext) -> FastAPI:
    """Build a minimal app that renders the real scoreboard template."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")
    web_dir = Path(__file__).resolve().parents[2] / "web"
    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    templates = Jinja2Templates(directory=web_dir / "template")
    templates.env.globals.update(
        {
            "app_version": "test",
            "brand_name": "NOCA",
            "healthmon_url": "",
            **template_globals(),
        }
    )
    templates.env.filters["utc_to_local"] = lambda value, _timezone: value
    setup_flash(templates)
    app.state.templates = templates
    app.state.valkey_runtime = object()

    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/css", StaticFiles(directory=web_dir / "static" / "css"), name="static_css")
    app.mount("/static/js", StaticFiles(directory=web_dir / "static" / "js"), name="static_js")
    app.mount("/static/shared/js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")
    app.mount("/static/img", StaticFiles(directory=web_dir / "static" / "img"), name="static_img")

    @app.get("/uberadmin", name="uberadmin_dashboard")
    @app.get("/logout", name="logout")
    @app.get("/profile", name="profile_get")
    async def _account_stub() -> dict[str, str]:
        """Provide account route names used by the base template."""
        return {"ok": "ok"}

    @app.get("/c/{slug}", name="contest_dashboard")
    @app.get("/c/{slug}/clock", name="contest_clock")
    async def _contest_stub(slug: str) -> dict[str, str]:
        """Provide contest route names used by the base template."""
        return {"slug": slug}

    app.include_router(contest_score.router)

    async def _override_ctx() -> ContestContext:
        """Return the requested scoreboard actor and contest."""
        return ctx

    app.dependency_overrides[get_contest_context] = _override_ctx
    return app


async def _create_site(session: AsyncSession, contest: Contest, name: str) -> Site:
    """Create and flush a contest site."""
    site = Site(
        contest_id=contest.id,
        sitename=name,
        sitename_normalized=normalize_site_name_key(name),
    )
    session.add(site)
    await session.flush()
    return site


def _snapshot(first_team: User, second_team: User) -> ScoreboardSnapshot:
    """Build a contest-wide snapshot with deliberately non-local ranks."""
    return ScoreboardSnapshot(
        contest_id=first_team.contest_id,
        generated_at="2026-07-24T12:00:00Z",
        is_frozen=False,
        standings=[
            TeamStanding(
                rank=2,
                team_id=first_team.id,
                team_name=first_team.username,
                team_fullname=first_team.fullname,
                problems_solved=2,
                total_time=100,
                problems={},
            ),
            TeamStanding(
                rank=5,
                team_id=second_team.id,
                team_name=second_team.username,
                team_fullname=second_team.fullname,
                problems_solved=1,
                total_time=150,
                problems={},
            ),
        ],
        problems=[],
        balloon_colors=[],
    )


async def _get_scoreboard(
    actor: UberAdmin | User,
    session: AsyncSession,
    contest: Contest,
    snapshot: ScoreboardSnapshot,
    *,
    site_id: str = "",
) -> str:
    """Render the scoreboard through its HTTP route and return the HTML."""
    app = _build_app(ContestContext(contest=contest, session=session, actor=actor))
    query = {"site_id": site_id} if site_id else None
    with patch.object(
        contest_score._service,
        "get_cached_or_compute",
        new=AsyncMock(return_value=snapshot),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get(f"/c/{contest.login_slug}/scoreboard/", params=query)
    assert response.status_code == 200
    return response.text


@pytest.mark.asyncio
async def test_assigned_user_may_filter_only_to_own_site(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    another_team_user: User,
) -> None:
    """Offer an assigned user all teams and only their own site."""
    own_site = await _create_site(session, running_contest, "Alpha")
    other_site = await _create_site(session, running_contest, "Beta")
    team_user.site_id = own_site.id
    another_team_user.site_id = other_site.id
    await session.flush()
    snapshot = _snapshot(team_user, another_team_user)

    all_sites_html = await _get_scoreboard(team_user, session, running_contest, snapshot)
    own_site_html = await _get_scoreboard(
        team_user,
        session,
        running_contest,
        snapshot,
        site_id=own_site.id,
    )

    assert team_user.fullname in all_sites_html
    assert another_team_user.fullname in all_sites_html
    assert "All sites" in all_sites_html
    assert "My site only" in all_sites_html
    assert "scoreboard-site-filter" not in all_sites_html
    assert f"?site_id={own_site.id}" in all_sites_html
    assert f"?site_id={other_site.id}" not in all_sites_html
    assert team_user.fullname in own_site_html
    assert another_team_user.fullname not in own_site_html
    assert f"?site_id={own_site.id}" in own_site_html
    assert "scoreboard-site-filter" not in own_site_html


@pytest.mark.asyncio
async def test_unassigned_user_may_select_any_site_and_keeps_global_rank(
    session: AsyncSession,
    running_contest: Contest,
    admin_user: User,
    team_user: User,
    another_team_user: User,
) -> None:
    """Expose every site to an unassigned user and preserve global ranks."""
    first_site = await _create_site(session, running_contest, "Alpha")
    second_site = await _create_site(session, running_contest, "Beta")
    team_user.site_id = first_site.id
    another_team_user.site_id = second_site.id
    await session.flush()

    html = await _get_scoreboard(
        admin_user,
        session,
        running_contest,
        _snapshot(team_user, another_team_user),
        site_id=second_site.id,
    )

    assert f'value="{first_site.id}"' in html
    assert f'value="{second_site.id}"' in html
    assert "scoreboard-site-filter" in html
    assert "My site only" not in html
    assert team_user.fullname not in html
    assert another_team_user.fullname in html
    assert '<td class="text-center fw-semibold noca-tabular-nums">5</td>' in html


@pytest.mark.asyncio
async def test_disallowed_or_unknown_site_falls_back_to_all_teams(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    another_team_user: User,
) -> None:
    """Use the all-sites view for a site outside the actor's option set."""
    own_site = await _create_site(session, running_contest, "Alpha")
    other_site = await _create_site(session, running_contest, "Beta")
    team_user.site_id = own_site.id
    another_team_user.site_id = other_site.id
    await session.flush()

    disallowed_html = await _get_scoreboard(
        team_user,
        session,
        running_contest,
        _snapshot(team_user, another_team_user),
        site_id=other_site.id,
    )
    unknown_html = await _get_scoreboard(
        team_user,
        session,
        running_contest,
        _snapshot(team_user, another_team_user),
        site_id="unknown-site",
    )

    for html in (disallowed_html, unknown_html):
        assert team_user.fullname in html
        assert another_team_user.fullname in html
        assert 'aria-current="page">All sites</a>' in html


@pytest.mark.asyncio
async def test_single_site_hides_filter_and_ignores_site_query(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
    team_user: User,
    another_team_user: User,
) -> None:
    """Hide unnecessary filtering and retain the all-sites scoreboard."""
    only_site = await _create_site(session, running_contest, "Gamma")

    html = await _get_scoreboard(
        uberadmin,
        session,
        running_contest,
        _snapshot(team_user, another_team_user),
        site_id=only_site.id,
    )

    assert "scoreboard-site-filter" not in html
    assert "My site only" not in html
    assert team_user.fullname in html
    assert another_team_user.fullname in html


@pytest.mark.asyncio
async def test_filter_copies_snapshot_without_changing_cached_rows(
    team_user: User,
    another_team_user: User,
) -> None:
    """Leave the contest-wide cached snapshot untouched when filtering rows."""
    snapshot = _snapshot(team_user, another_team_user)

    filtered = contest_score._filter_snapshot_by_site(
        snapshot,
        {
            team_user.id: "alpha",
            another_team_user.id: "beta",
        },
        "beta",
    )

    assert filtered is not snapshot
    assert [standing.team_id for standing in filtered.standings] == [another_team_user.id]
    assert filtered.standings[0].rank == 5
    assert [standing.team_id for standing in snapshot.standings] == [
        team_user.id,
        another_team_user.id,
    ]
