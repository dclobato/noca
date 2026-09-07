#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The single-session policy as it is felt by a request rather than by a row.

`test_session_policy.py` pins the rule; these tests pin the four things only a
request can show: that the check runs in front of *every* authenticated route
rather than only the ones that resolve an actor, that a rejected session is not
handed a rotated cookie on its way out, that the first request after the start
binds the seat with no login involved, and that the ungoverned majority pay one
query for the privilege.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from time import time
from typing import cast

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi_flash import FlashService, setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from shared.db_schema import users as users_t
from shared.enumerations import RoleEnum
from shared.services.geolocation import GeolocationDetails, GeolocationIP
from web.config import settings
from web.dependencies import enforce_web_default_auth, get_request_user
from web.middleware.auth_token_refresh import AuthTokenRefreshMiddleware
from web.models import Contest, UberAdmin, User
from web.routes.session import router as session_router
from web.services.authentication_service import AuthAction, AuthenticationService
from web.services.session_policy import SESSION_EPOCH_CLAIM

TEST_JWT_SECRET = "test-secret-key-for-tests-only-32bytes"
AUTH_COOKIE = "noca_access_token"
_VENUE_IP = "127.0.0.1"  # the address the test client presents
_HOME_IP = "198.51.100.4"


class _NoopGeo:
    def get_details_by_ip(self, ip_address: str | None) -> GeolocationDetails | None:
        return None


def _build_app(session: AsyncSession) -> tuple[FastAPI, AuthenticationService]:
    """The smallest app carrying the production default-auth dependency."""
    app = FastAPI(dependencies=[Depends(enforce_web_default_auth)])
    app.state.db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")
    app.add_middleware(AuthTokenRefreshMiddleware)

    templates = Jinja2Templates(directory="web/template")
    setup_flash(templates)
    app.state.templates = templates

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
    app.include_router(session_router)

    @app.get("/login", name="login_get")
    async def _login() -> dict[str, str]:
        return {"page": "login"}

    @app.get("/c/{slug}/login", name="contest_login_get")
    async def _contest_login(request: Request, slug: str) -> dict[str, object]:
        """The public page a refused session lands on, showing why it got there."""
        return {"messages": FlashService(request).get_flashed_messages(with_categories=True)}

    @app.get("/c/{slug}/runs", name="contest_runs")
    async def _runs(slug: str) -> dict[str, str]:
        return {"page": "runs"}

    @app.get("/profile", name="profile_get")
    async def _profile(user: User = Depends(get_request_user)) -> dict[str, str]:
        return {"user": user.username}

    return app, app.state.auth_service


def _token(auth_service: AuthenticationService, user: User, *, epoch: int | None) -> str:
    """Mint a contest token, optionally without the epoch claim (a legacy one)."""
    extra: dict[str, object] = {"contest_id": user.contest_id, "session_started_at": int(time())}
    if epoch is not None:
        extra[SESSION_EPOCH_CLAIM] = epoch
    return cast(
        str,
        auth_service.jwt_service.create(
            action=AuthAction.WEB_ACCESS,
            sub=user.username,
            audience=user.role.value,
            extra_data=extra,
            expires_in=600,
        ),
    )


def _refreshed_cookie(response: object) -> str | None:
    jar = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):  # type: ignore[attr-defined]
        jar.load(header)
    morsel = jar.get(AUTH_COOKIE)
    return morsel.value if morsel else None


def _restrict(user: User) -> User:
    user.allow_concurrent_login = False
    return user


@pytest.mark.asyncio
async def test_a_bound_team_is_allowed_from_its_own_seat(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    _restrict(team_user)
    team_user.locked_ip = _VENUE_IP
    team_user.session_epoch = 4
    await session.commit()
    app, auth_service = _build_app(session)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={AUTH_COOKIE: _token(auth_service, team_user, epoch=4)},
    ) as client:
        response = await client.get(f"/c/{running_contest.login_slug}/runs")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_a_copied_cookie_does_not_work_from_another_address(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """The whole point of the feature: the IP rule holds per request, not per login."""
    _restrict(team_user)
    team_user.locked_ip = _HOME_IP
    team_user.session_epoch = 1
    await session.commit()
    app, auth_service = _build_app(session)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={AUTH_COOKIE: _token(auth_service, team_user, epoch=1)},
    ) as client:
        response = await client.get(f"/c/{running_contest.login_slug}/runs", follow_redirects=False)
        assert response.headers["location"] == f"/c/{running_contest.login_slug}/login"
        landing = await client.get(response.headers["location"])

    assert response.status_code == 302
    assert _HOME_IP in str(landing.json()["messages"]), "the message names the seat to sign in from"


@pytest.mark.asyncio
async def test_a_superseded_session_is_signed_out_on_its_next_request(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    _restrict(team_user)
    team_user.locked_ip = _VENUE_IP
    team_user.session_epoch = 7
    await session.commit()
    app, auth_service = _build_app(session)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={AUTH_COOKIE: _token(auth_service, team_user, epoch=6)},
    ) as client:
        response = await client.get(f"/c/{running_contest.login_slug}/runs", follow_redirects=False)
        landing = await client.get(response.headers["location"])

    assert response.status_code == 302
    assert "another computer" in str(landing.json()["messages"])


@pytest.mark.asyncio
async def test_a_rejected_session_is_not_handed_a_fresh_cookie(
    session: AsyncSession, running_contest: Contest, team_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordering the design review caught.

    `enforce_web_default_auth` used to mark the session refresh-eligible on token
    validity alone, before any actor check, so a session the policy rejects still
    left with a rotated cookie -- a sign-out that renewed itself.
    """
    monkeypatch.setattr(settings, "JWT_EXPIRE_SECONDS", 3600)
    _restrict(team_user)
    team_user.locked_ip = _HOME_IP
    await session.commit()
    app, auth_service = _build_app(session)
    token = cast(
        str,
        auth_service.jwt_service.create(
            action=AuthAction.WEB_ACCESS,
            sub=team_user.username,
            audience=team_user.role.value,
            extra_data={"contest_id": team_user.contest_id, SESSION_EPOCH_CLAIM: 0, "session_started_at": int(time())},
            expires_in=600,  # inside the half-life window, so a refresh is due
        ),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={AUTH_COOKIE: token},
    ) as client:
        response = await client.get(f"/c/{running_contest.login_slug}/runs", follow_redirects=False)

    assert response.status_code == 302
    assert _refreshed_cookie(response) is None


@pytest.mark.asyncio
async def test_the_heartbeat_is_governed_like_every_other_route(
    session: AsyncSession, running_contest: Contest, team_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route that resolves no actor is exactly the one that must not be exempt.

    Its whole purpose is to extend a session, so a rule living in the actor
    resolvers would have left it able to keep a superseded session alive
    indefinitely.
    """
    monkeypatch.setattr(settings, "JWT_EXPIRE_SECONDS", 3600)
    _restrict(team_user)
    team_user.locked_ip = _VENUE_IP
    team_user.session_epoch = 2
    await session.commit()
    app, auth_service = _build_app(session)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={AUTH_COOKIE: _token(auth_service, team_user, epoch=1)},
    ) as client:
        response = await client.post("/session/heartbeat", follow_redirects=False)

    assert response.status_code == 302
    assert _refreshed_cookie(response) is None


@pytest.mark.asyncio
async def test_the_first_request_after_the_start_binds_the_seat_with_no_login(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """A session opened before the contest keeps working, and binds where it is.

    The team that logged in last at home and then walked to the venue does not
    have to sign in again at the gun; its first request there is the binding.
    """
    _restrict(team_user)
    await session.commit()
    assert team_user.locked_ip is None
    app, auth_service = _build_app(session)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={AUTH_COOKIE: _token(auth_service, team_user, epoch=0)},
    ) as client:
        response = await client.get(f"/c/{running_contest.login_slug}/runs")

    assert response.status_code == 200
    bound = await session.scalar(select(users_t.c.locked_ip).where(users_t.c.id == team_user.id))
    assert bound == _VENUE_IP


@pytest.mark.asyncio
async def test_an_ungoverned_request_never_loads_the_contest(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """The guard sits in front of every authenticated route, so it must stay cheap.

    A user the policy could never govern is settled by the user row alone -- the
    one the resolver was going to load anyway -- with no contest query and no
    second lookup of the user.
    """
    await session.commit()
    assert team_user.allow_concurrent_login is True
    app, auth_service = _build_app(session)
    statements: list[str] = []

    @event.listens_for(session.bind.sync_engine, "before_cursor_execute")  # type: ignore[union-attr]
    def _record(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        statements.append(statement)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            cookies={AUTH_COOKIE: _token(auth_service, team_user, epoch=0)},
        ) as client:
            response = await client.get("/profile")
    finally:
        event.remove(session.bind.sync_engine, "before_cursor_execute", _record)  # type: ignore[union-attr]

    assert response.status_code == 200
    assert response.json() == {"user": team_user.username}
    assert not [statement for statement in statements if "FROM contests" in statement]
    assert len([statement for statement in statements if "FROM users" in statement]) == 1


@pytest.mark.asyncio
async def test_staff_are_governed_by_their_role_not_by_the_flag(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """An organiser whose flag the bulk toggle cleared must still get in."""
    judge = User(
        username="judge_bound",
        fullname="Judge",
        role=RoleEnum.JUDGE,
        contest_id=running_contest.id,
        created_by_uberadmin_id=uberadmin.id,
        allow_concurrent_login=False,
        locked_ip=_HOME_IP,
        locked_at=datetime.now(UTC) - timedelta(minutes=5),
        session_epoch=9,
    )
    judge.password = "TestPass1!"
    session.add(judge)
    await session.commit()
    app, auth_service = _build_app(session)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={AUTH_COOKIE: _token(auth_service, judge, epoch=1)},
    ) as client:
        response = await client.get(f"/c/{running_contest.login_slug}/runs")

    assert response.status_code == 200, "a stale epoch and a foreign binding are both ignored for staff"
