#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route coverage for the team status board.

The page is open to everyone running the venue and closed to teams, polls its
own URL through htmx every ten seconds, and renders each team's state three
ways. These tests render the real template through the smallest application
that can serve it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from tests.conftest import _make_user
from tests.web._contest_admin_test_support import build_contest_admin_app
from web.config import settings
from web.models.contest import Contest
from web.models.site import Site
from web.models.users import Login_History, UberAdmin, User
from web.routes.contest_admin_user_edit import router as edit_router
from web.routes.contest_team_status import router
from web.services.authentication_service import AuthAction, AuthenticationService

AUTH_COOKIE = "noca_access_token"


class _FakePresence:
    """Answers ``mget`` from a ``{user_id: value}`` map; ``eval`` feeds the read ceiling's fallback."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}
        self.eval = AsyncMock(return_value=None)

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.values.get(key.rsplit(":", 1)[1]) for key in keys]


def _token(auth_service: AuthenticationService, *, username: str, role: RoleEnum, contest_id: str) -> str:
    return auth_service.jwt_service.create(
        action=AuthAction.WEB_ACCESS,
        sub=username,
        audience=role.value,
        extra_data={"contest_id": contest_id, "session_started_at": int(datetime.now(UTC).timestamp())},
    )


async def _get(
    session: AsyncSession,
    contest: Contest,
    *,
    username: str,
    role: RoleEnum,
    presence: _FakePresence | None = None,
    query: str = "",
    routers: tuple[APIRouter, ...] = (router,),
) -> tuple[int, str]:
    await session.commit()
    app, auth_service = build_contest_admin_app(session, routers=routers)
    app.state.valkey_runtime = presence or _FakePresence()
    token = _token(auth_service, username=username, role=role, contest_id=contest.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set(AUTH_COOKIE, token)
        response = await client.get(f"/c/{contest.login_slug}/team-status{query}")
    return response.status_code, response.text


@pytest.mark.asyncio
async def test_the_board_renders_every_state_with_its_three_channels(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
    admin_user: User,
    team_user: User,
    another_team_user: User,
) -> None:
    """Tint class, icon and word for online, offline and never; the address beside each."""
    ghost = _make_user(session, running_contest, uberadmin, "team_c", "Team C", RoleEnum.TEAM)
    await session.flush()
    session.add(
        Login_History(
            user_id=another_team_user.id,
            dta_login=running_contest.start_time + timedelta(minutes=1),
            ip_address="10.0.0.2",
        )
    )
    await session.flush()

    status, html = await _get(
        session,
        running_contest,
        username=admin_user.username,
        role=RoleEnum.ADMIN,
        presence=_FakePresence({team_user.id: "203.0.113.5"}),
    )

    assert status == 200
    assert 'id="team-status-grid"' in html
    assert 'hx-trigger="every 10s"' in html
    assert f'/c/{running_contest.login_slug}/team-status"' in html
    assert "noca-team-status-board--running" in html
    # Empty seats are rendered; the present team is folded into its count.
    for username, state in (("team_b", "offline"), (ghost.username, "never")):
        assert f"noca-team-status-card--{state}" in html
        assert f'<code class="noca-team-status-card-username">{username}</code>' in html
    assert "noca-team-status-card--online" not in html
    assert "Team A" not in html and "Team B" in html and "Team C" in html
    assert "1 online" in html and 'aria-expanded="false"' in html
    assert 'aria-controls="team-status-folded-' not in html  # nothing to control while the fold is closed
    assert "<code>10.0.0.2</code>" in html and "(last login)" in html
    assert "Never signed in" in html and "wifi_off" in html and "person_off" in html
    assert f"/user/{another_team_user.id}/avatar?v=" in html
    # The poll re-fetches the current URL (path and query, never the host), so
    # scope and unfolded sites survive it and a misconfigured proxy cannot break it.
    assert f'hx-get="/c/{running_contest.login_slug}/team-status"' in html


@pytest.mark.asyncio
async def test_show_unfolds_a_sites_online_teams_and_the_poll_keeps_it(
    session: AsyncSession, running_contest: Contest, admin_user: User, team_user: User
) -> None:
    """`show=<site>` renders that site's online cards; the fold link drops it again."""
    status, html = await _get(
        session,
        running_contest,
        username=admin_user.username,
        role=RoleEnum.ADMIN,
        presence=_FakePresence({team_user.id: "203.0.113.5"}),
        query="?show=unassigned",
    )

    assert status == 200
    assert "noca-team-status-card--online" in html
    assert "Team A" in html and "<code>203.0.113.5</code>" in html
    assert 'aria-expanded="true"' in html and 'aria-controls="team-status-folded-unassigned"' in html
    assert f'hx-get="/c/{running_contest.login_slug}/team-status?show=unassigned"' in html
    assert f'href="/c/{running_contest.login_slug}/team-status"' in html  # the fold link, show removed
    assert 'hx-push-url="true"' in html


@pytest.mark.asyncio
async def test_before_the_start_the_board_is_quiet_until_a_site_is_unfolded(
    session: AsyncSession, running_contest: Contest, admin_user: User, team_user: User
) -> None:
    """Pre-start, teams not signed in yet fold with the online ones; unfolding shows them, worded as expected."""
    running_contest.start_time = running_contest.start_time + timedelta(hours=5)
    await session.flush()

    status, folded = await _get(session, running_contest, username=admin_user.username, role=RoleEnum.ADMIN)
    _status, unfolded = await _get(
        session, running_contest, username=admin_user.username, role=RoleEnum.ADMIN, query="?show=unassigned"
    )

    assert status == 200
    assert "noca-team-status-board--before" in folded
    assert "noca-team-status-card--never" not in folded
    assert "not signed in yet" in folded and "Never signed in" not in folded
    assert "noca-team-status-card--never" in unfolded and "Not signed in yet" in unfolded


@pytest.mark.asyncio
async def test_site_scopes_the_board_and_an_unknown_site_means_all(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin, admin_user: User, team_user: User
) -> None:
    """One site at a time when asked; a stale scope falls back to every site."""
    hall = Site(sitename="Hall", sitename_normalized="hall", contest_id=running_contest.id)
    session.add(hall)
    await session.flush()
    housed = _make_user(session, running_contest, uberadmin, "team_h", "Team Housed", RoleEnum.TEAM)
    await session.flush()
    housed.site_id = hall.id
    await session.flush()

    status, scoped = await _get(
        session, running_contest, username=admin_user.username, role=RoleEnum.ADMIN, query=f"?site={hall.id}"
    )
    _status, unscoped = await _get(
        session, running_contest, username=admin_user.username, role=RoleEnum.ADMIN, query="?site=nope"
    )

    assert status == 200
    assert "Team Housed" in scoped and "Team A" not in scoped
    assert "Site: Hall" in scoped
    assert "Team Housed" in unscoped and "Team A" in unscoped
    assert "Site: All sites" in unscoped


@pytest.mark.asyncio
async def test_admins_get_a_link_to_the_record_and_judges_do_not(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
    admin_user: User,
    judge_user: User,
    team_user: User,
) -> None:
    """The enrolled-user page is closed to judges, so they get no link to a refusal."""
    team_user.location = "Lab 3"
    await session.flush()

    # The real edit router is mounted so `url_for('edit_user_form', ...)` resolves,
    # as it does in the application; a single-router app renders no link at all.
    _status, as_admin = await _get(
        session, running_contest, username=admin_user.username, role=RoleEnum.ADMIN, routers=(router, edit_router)
    )
    _status, as_judge = await _get(session, running_contest, username=judge_user.username, role=RoleEnum.JUDGE)

    assert "noca-team-status-card-link" in as_admin
    assert "noca-team-status-card-link" not in as_judge
    assert "Lab 3" in as_admin and "Lab 3" in as_judge


@pytest.mark.parametrize("role", [RoleEnum.ADMIN, RoleEnum.JUDGE, RoleEnum.STAFF])
@pytest.mark.asyncio
async def test_venue_roles_can_open_the_board(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin, team_user: User, role: RoleEnum
) -> None:
    """Admins, judges and staff all run the venue; none of them is turned away."""
    viewer = _make_user(session, running_contest, uberadmin, f"viewer_{role.value}", "Viewer", role)
    await session.flush()

    status, html = await _get(session, running_contest, username=viewer.username, role=role)

    assert status == 200
    assert "Team A" in html


@pytest.mark.asyncio
async def test_the_uberadmin_can_open_the_board(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin, team_user: User
) -> None:
    status, html = await _get(session, running_contest, username=uberadmin.username, role=RoleEnum.UBERADMIN)

    assert status == 200
    assert "Team A" in html


@pytest.mark.asyncio
async def test_a_team_is_refused(session: AsyncSession, running_contest: Contest, team_user: User) -> None:
    """Teams do not get to watch each other's seats."""
    status, _html = await _get(session, running_contest, username=team_user.username, role=RoleEnum.TEAM)

    assert status == 403


@pytest.mark.asyncio
async def test_presence_off_is_said_on_the_page(
    session: AsyncSession,
    running_contest: Contest,
    admin_user: User,
    team_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With presence disabled nobody is online, and the page explains why."""
    monkeypatch.setattr(settings, "PRESENCE_ENABLED", False)

    status, html = await _get(
        session,
        running_contest,
        username=admin_user.username,
        role=RoleEnum.ADMIN,
        presence=_FakePresence({team_user.id: "203.0.113.5"}),
    )

    assert status == 200
    assert "Presence tracking is off" in html
    assert "noca-team-status-card--online" not in html
