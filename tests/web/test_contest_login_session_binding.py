#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""What the contest login does once the single-session policy governs a team.

The policy module owns the rule and its concurrency; these tests cover the two
things only the login path can show: that the token a login mints carries the
epoch the database committed for it, and that a refusal from a foreign address
is reported as itself rather than as a wrong password -- which is what keeps it
off the credentials throttle and tells the team what to ask the staff for.
"""

from __future__ import annotations

import pytest
from httpx import URL, ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import security_events
from shared.db_schema import users as users_t
from tests.shared._auth_fake_valkey import AuthFakeValkey
from tests.web.test_inactive_contest_routes import _build_app
from web.models.contest import Contest
from web.models.users import Login_History, User
from web.services.session_policy import SESSION_EPOCH_CLAIM

_CLIENT_IP = "127.0.0.1"
_HOME_IP = "198.51.100.4"


async def _login(app, slug: str, *, follow_redirects: bool = False):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        return await client.post(
            f"/c/{slug}/login",
            data={"identifier": "team_a", "password": "TestPass1!"},
            follow_redirects=follow_redirects,
        )


async def _epoch(session: AsyncSession, user_id: str) -> int:
    return int(await session.scalar(select(users_t.c.session_epoch).where(users_t.c.id == user_id)))


@pytest.mark.asyncio
async def test_a_login_stamps_the_epoch_it_committed_into_the_token(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """The claim comes from the write, never from a value read before it."""
    await session.commit()
    app, auth_service = _build_app(session)
    app.state.valkey_runtime = AuthFakeValkey()

    response = await _login(app, running_contest.login_slug)

    assert response.status_code == 303
    token = response.cookies["noca_access_token"]
    result = auth_service.jwt_service.validate(token)
    assert result.valid is True
    assert result.extra_data is not None
    assert result.extra_data[SESSION_EPOCH_CLAIM] == await _epoch(session, team_user.id) == 1


@pytest.mark.asyncio
async def test_a_governed_first_login_binds_the_seat_it_came_from(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    team_user.allow_concurrent_login = False
    await session.commit()
    app, _auth_service = _build_app(session)
    app.state.valkey_runtime = AuthFakeValkey()

    response = await _login(app, running_contest.login_slug)

    assert response.status_code == 303
    assert response.headers["location"] == f"/c/{running_contest.login_slug}"
    await session.refresh(team_user)
    assert team_user.locked_ip == _CLIENT_IP
    assert team_user.locked_at is not None


@pytest.mark.asyncio
async def test_a_login_from_outside_the_bound_seat_is_refused_by_name(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """The password was right, so the page must not say it was wrong.

    It also must not count as a credentials failure, or a team trying from the
    wrong machine would lock out the seat that is actually competing.
    """
    team_user.allow_concurrent_login = False
    team_user.locked_ip = _HOME_IP
    await session.commit()
    epoch_before = await _epoch(session, team_user.id)
    app, _auth_service = _build_app(session)
    app.state.valkey_runtime = AuthFakeValkey()

    response = await _login(app, running_contest.login_slug, follow_redirects=True)

    assert response.status_code == 200
    assert _HOME_IP in response.text
    assert "clear the IP lock" in response.text
    assert "Invalid username or password." not in response.text

    await session.refresh(team_user)
    assert team_user.locked_ip == _HOME_IP, "the competing seat keeps its binding"
    assert await _epoch(session, team_user.id) == epoch_before, "and its live session is not superseded"
    assert (
        await session.scalar(
            select(func.count()).select_from(Login_History).where(Login_History.user_id == team_user.id)
        )
        == 0
    ), "a refused login opened no session, so it is not device history"
    events = (
        (await session.execute(select(security_events.c.event_type).where(security_events.c.module == "web")))
        .scalars()
        .all()
    )
    assert "auth_session_ip_locked" in events
    assert "auth_failure" not in events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("password", "expectation"),
    [("TestPass1!", "the policy refusal"), ("wrong-password", "the credentials failure")],
)
async def test_a_refused_login_keeps_a_multi_parameter_return_page_intact(
    session: AsyncSession, running_contest: Contest, team_user: User, password: str, expectation: str
) -> None:
    """Both refusal branches carry `next` as a query parameter, so `&` survives.

    Interpolating it into the URL by hand let the second parameter of the
    destination be read as a parameter of the login page instead, and the team
    came back to a truncated page.
    """
    team_user.allow_concurrent_login = False
    team_user.locked_ip = _HOME_IP
    await session.commit()
    next_url = f"/c/{running_contest.login_slug}/runs?status=done&lang=py"
    app, _auth_service = _build_app(session)
    app.state.valkey_runtime = AuthFakeValkey()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/c/{running_contest.login_slug}/login",
            data={"identifier": "team_a", "password": password, "next_url": next_url},
        )

    assert response.status_code == 303, expectation
    location = URL(response.headers["location"])
    assert location.path == f"/c/{running_contest.login_slug}/login"
    assert dict(location.params)["next"] == next_url
