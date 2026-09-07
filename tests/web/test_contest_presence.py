#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Web marks a team present on its ordinary requests (#219).

The read side is decoration unless something writes the marker, and what writes
it is the contest clock: every contest page re-fetches `GET /c/{slug}/clock`
once a minute through `_base.html`, and that request is authenticated. These
tests pin that the write happens on such a request, under the domain the shared
reader looks in, and that every reason to skip it is a quiet no-op.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from time import time
from typing import Any, cast

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from shared.enumerations import RoleEnum
from shared.services.geolocation import GeolocationDetails, GeolocationIP
from shared.services.team_absence_status import CONTEST_PRESENCE_DOMAIN
from shared.services.user_presence import user_live_key
from tests.web.test_inactive_contest_routes import (
    _AuthThrottleValkeyRuntime,
    _contest_token,
)
from tests.web.test_inactive_contest_routes import (
    _build_app as _build_auth_app,
)
from web.config import settings
from web.dependencies import enforce_web_default_auth
from web.models.contest import Contest
from web.models.users import UberAdmin, User
from web.services.authentication_service import AuthAction, AuthenticationService

TEST_JWT_SECRET = "test-secret-key-for-tests-only-32bytes"
AUTH_COOKIE = "noca_access_token"


class _NoopGeo:
    def get_details_by_ip(self, ip_address: str | None) -> GeolocationDetails | None:
        return None


class _RecordingValkey:
    """Captures the presence script's key arguments without a real Valkey."""

    def __init__(self) -> None:
        self.marked: list[str] = []
        self.calls: list[tuple[Any, ...]] = []

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        self.marked.append(str(args[0]))
        self.calls.append(args)
        return 1


def _build_app(session: AsyncSession) -> tuple[FastAPI, AuthenticationService]:
    app = FastAPI(dependencies=[Depends(enforce_web_default_auth)])
    app.state.db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    jwt_service = JWTService(
        config=load_token_config_from_dict(
            {
                "SECRET_KEY": TEST_JWT_SECRET,
                "JWTSERVICE_ALGORITHM": "HS256",
                "JWTSERVICE_ISSUER": "noca-test",
            }
        ),
        logger=logging.getLogger(__name__),
        action_enum=AuthAction,
    )
    app.state.auth_service = AuthenticationService(
        jwt_service=jwt_service,
        geolocation_service=cast(GeolocationIP, _NoopGeo()),
        logger=logging.getLogger(__name__),
    )

    @app.get("/login", name="login_get")
    async def _login() -> dict[str, str]:
        return {"page": "login"}

    @app.get("/c/{slug}/login", name="contest_login_get")
    async def _contest_login(slug: str) -> dict[str, str]:
        return {"page": "contest-login"}

    @app.get("/c/{slug}/clock", name="contest_clock")
    async def _clock(slug: str) -> dict[str, str]:
        return {"page": "clock"}

    @app.post("/c/{slug}/runs", name="contest_runs_submit")
    async def _submit(slug: str) -> dict[str, str]:
        return {"page": "submit"}

    return app, app.state.auth_service


def _token(auth_service: AuthenticationService, user: User) -> str:
    return cast(
        str,
        auth_service.jwt_service.create(
            action=AuthAction.WEB_ACCESS,
            sub=user.username,
            audience=user.role.value,
            extra_data={"contest_id": user.contest_id, "session_started_at": int(time())},
            expires_in=600,
        ),
    )


async def _member(session: AsyncSession, contest: Contest, uberadmin: UberAdmin, username: str, role: RoleEnum) -> User:
    user = User(
        username=username,
        fullname=username,
        role=role,
        contest_id=contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_the_clock_poll_marks_the_team_present(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """The carrier: one authenticated GET a minute from every contest page."""
    await session.commit()
    app, auth_service = _build_app(session)
    valkey = _RecordingValkey()
    app.state.valkey_runtime = valkey

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={AUTH_COOKIE: _token(auth_service, team_user)},
    ) as client:
        response = await client.get(f"/c/{running_contest.login_slug}/clock")

    assert response.status_code == 200
    assert valkey.marked == [user_live_key(CONTEST_PRESENCE_DOMAIN, team_user.id)]


@pytest.mark.asyncio
async def test_the_key_is_the_one_the_reader_looks_in(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """Writer and reader must agree on the domain, or the board is always absent."""
    await session.commit()
    app, auth_service = _build_app(session)
    valkey = _RecordingValkey()
    app.state.valkey_runtime = valkey

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={AUTH_COOKIE: _token(auth_service, team_user)},
    ) as client:
        await client.get(f"/c/{running_contest.login_slug}/clock")

    assert valkey.marked[0].startswith(f"noca:user-presence:{CONTEST_PRESENCE_DOMAIN}:live:")


@pytest.mark.asyncio
async def test_the_marker_carries_the_client_address(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """The live key's value is where the team is, for the team status map to show."""
    await session.commit()
    app, auth_service = _build_app(session)
    valkey = _RecordingValkey()
    app.state.valkey_runtime = valkey

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={AUTH_COOKIE: _token(auth_service, team_user)},
    ) as client:
        await client.get(f"/c/{running_contest.login_slug}/clock")

    # eval args: live key, online set key, ttl, score, user id, value.
    (args,) = valkey.calls
    assert args[4] == team_user.id
    assert args[5] == "127.0.0.1"


@pytest.mark.asyncio
async def test_staff_are_not_marked(session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin) -> None:
    """No other role is ever shown as absent, so marking them buys nothing."""
    judge = await _member(session, running_contest, uberadmin, "judge_p", RoleEnum.JUDGE)
    await session.commit()
    app, auth_service = _build_app(session)
    valkey = _RecordingValkey()
    app.state.valkey_runtime = valkey

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={AUTH_COOKIE: _token(auth_service, judge)},
    ) as client:
        await client.get(f"/c/{running_contest.login_slug}/clock")

    assert valkey.marked == []


@pytest.mark.asyncio
async def test_a_write_request_does_not_mark(session: AsyncSession, running_contest: Contest, team_user: User) -> None:
    """`GET` only, as Arena does: the clock covers the gap within the minute."""
    await session.commit()
    app, auth_service = _build_app(session)
    valkey = _RecordingValkey()
    app.state.valkey_runtime = valkey

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={AUTH_COOKIE: _token(auth_service, team_user)},
    ) as client:
        await client.post(f"/c/{running_contest.login_slug}/runs")

    assert valkey.marked == []


@pytest.mark.asyncio
async def test_an_anonymous_request_marks_nobody(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    await session.commit()
    app, _auth_service = _build_app(session)
    valkey = _RecordingValkey()
    app.state.valkey_runtime = valkey

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.get(f"/c/{running_contest.login_slug}/login")

    assert valkey.marked == []


@pytest.mark.asyncio
async def test_presence_disabled_writes_nothing(
    session: AsyncSession, running_contest: Contest, team_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The switch is a real switch, not just a read-side filter."""
    monkeypatch.setattr(settings, "PRESENCE_ENABLED", False)
    await session.commit()
    app, auth_service = _build_app(session)
    valkey = _RecordingValkey()
    app.state.valkey_runtime = valkey

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={AUTH_COOKIE: _token(auth_service, team_user)},
    ) as client:
        await client.get(f"/c/{running_contest.login_slug}/clock")

    assert valkey.marked == []


@pytest.mark.asyncio
async def test_a_valkey_failure_does_not_fail_the_request(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """Presence decorates a display hint; it must never be able to break a page."""

    class _BrokenValkey:
        async def eval(self, *args: Any, **kwargs: Any) -> int:
            raise RuntimeError("valkey is unreachable")

    await session.commit()
    app, auth_service = _build_app(session)
    app.state.valkey_runtime = _BrokenValkey()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={AUTH_COOKIE: _token(auth_service, team_user)},
    ) as client:
        response = await client.get(f"/c/{running_contest.login_slug}/clock")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_a_team_of_an_ended_contest_is_still_marked(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Marking is not gated on the contest window; only the *reading* surface is.

    Keeping the write unconditional means the marker is already warm the moment
    a contest opens, rather than blank for one poll interval.
    """
    ended = Contest(
        contest_name="Ended",
        contest_url="http://ended.example.com",
        login_slug="ended-contest",
        start_time=datetime.now(UTC) - timedelta(hours=5),
        duration_minutes=120,
        stop_answers_after=120,
        stop_updating_scoreboard=120,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(ended)
    await session.flush()
    team = await _member(session, ended, uberadmin, "late_team", RoleEnum.TEAM)
    await session.commit()
    app, auth_service = _build_app(session)
    valkey = _RecordingValkey()
    app.state.valkey_runtime = valkey

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={AUTH_COOKIE: _token(auth_service, team)},
    ) as client:
        await client.get(f"/c/{ended.login_slug}/clock")

    assert valkey.marked == [user_live_key(CONTEST_PRESENCE_DOMAIN, team.id)]


class _RecordingAuthValkey(_AuthThrottleValkeyRuntime):
    """The auth-throttle double, also keeping every script call the logout route makes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def eval(self, script: str, numkeys: int, *args: Any) -> object | None:
        self.calls.append((script, args))
        return None


@pytest.mark.asyncio
async def test_logout_drops_the_marker_at_once(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """Pressing Logout is the one certain release of a seat: no three-minute afterglow."""
    await session.commit()
    app, auth_service = _build_auth_app(session)
    valkey = _RecordingAuthValkey()
    app.state.valkey_runtime = valkey
    token = _contest_token(auth_service, username=team_user.username, contest_id=running_contest.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set(AUTH_COOKIE, token)
        response = await client.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    offline_calls = [(script, args) for script, args in valkey.calls if "DEL" in script]
    assert len(offline_calls) == 1
    _script, args = offline_calls[0]
    assert args[0] == user_live_key(CONTEST_PRESENCE_DOMAIN, team_user.id)


@pytest.mark.asyncio
async def test_logout_without_a_session_touches_no_marker(session: AsyncSession, running_contest: Contest) -> None:
    """A logout with no valid token has nobody to clear."""
    await session.commit()
    app, _auth_service = _build_auth_app(session)
    valkey = _RecordingAuthValkey()
    app.state.valkey_runtime = valkey

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert not [script for script, _args in valkey.calls if "DEL" in script]
