import logging
from http.cookies import SimpleCookie
from time import time
from typing import Annotated, cast
from uuid import uuid4

import jwt
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi_flash import FlashService
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from shared.enumerations import RoleEnum
from shared.services.geolocation import GeolocationIP
from web.config import settings
from web.dependencies import get_request_user, get_uberadmin
from web.middleware.auth_token_refresh import AuthTokenRefreshMiddleware
from web.models.users import Login_History, UberAdmin, User
from web.services.authentication_service import AuthAction, AuthenticationService
from web.services.session_service import SESSION_EXPIRED_MESSAGE

TEST_JWT_SECRET = "test-secret-key-for-tests-only-32bytes"


class _NoopGeo:
    def get_location_by_ip(self, ip_address: str | None) -> str | None:
        return None


def _build_auth_app(session: AsyncSession) -> tuple[FastAPI, AuthenticationService]:
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")
    app.add_middleware(AuthTokenRefreshMiddleware)

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
    app.state.db_session = async_sessionmaker(session.bind, expire_on_commit=False)

    @app.get("/login", name="login_get")
    async def _login(request: Request) -> dict[str, object]:
        return {"messages": FlashService(request).get_flashed_messages(with_categories=True)}

    @app.get("/c/{slug}/login", name="contest_login_get")
    async def _contest_login(request: Request, slug: str) -> dict[str, object]:
        return {
            "slug": slug,
            "messages": FlashService(request).get_flashed_messages(with_categories=True),
        }

    @app.get("/contests", name="contests_list")
    async def _contests() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/protected")
    async def _protected(user: Annotated[User, Depends(get_request_user)]) -> dict[str, str]:
        return {"username": user.username}

    @app.get("/uberprotected")
    async def _uberprotected(user: Annotated[UberAdmin, Depends(get_uberadmin)]) -> dict[str, str]:
        return {"username": user.username}

    return app, app.state.auth_service


def _extract_cookie_value(response, name: str) -> str | None:
    cookie = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookie.load(header)
    morsel = cookie.get(name)
    return morsel.value if morsel else None


def _build_contest_token(
    auth_service: AuthenticationService,
    *,
    username: str,
    contest_id: str,
    expires_in: int,
    session_started_at: int | None,
) -> str:
    extra_data: dict[str, object] = {"contest_id": contest_id}
    if session_started_at is not None:
        extra_data["session_started_at"] = session_started_at
    return auth_service.jwt_service.create(
        action=AuthAction.WEB_ACCESS,
        sub=username,
        audience=RoleEnum.TEAM.value,
        extra_data=extra_data,
        expires_in=expires_in,
    )


def _build_expired_contest_token(
    *,
    username: str,
    contest_id: str,
    session_started_at: int,
) -> str:
    now = int(time())
    return jwt.encode(
        payload={
            "sub": username,
            "iat": now - 20,
            "nbf": now - 20,
            "exp": now - 10,
            "iss": "noca-test",
            "jti": str(uuid4()),
            "action": AuthAction.WEB_ACCESS.name,
            "aud": RoleEnum.TEAM.value,
            "extra_data": {
                "contest_id": contest_id,
                "session_started_at": session_started_at,
            },
        },
        key=TEST_JWT_SECRET,
        algorithm="HS256",
    )


def _build_expired_uberadmin_token(*, username: str) -> str:
    now = int(time())
    return jwt.encode(
        payload={
            "sub": username,
            "iat": now - 20,
            "nbf": now - 20,
            "exp": now - 10,
            "iss": "noca-test",
            "jti": str(uuid4()),
            "action": AuthAction.WEB_ACCESS.name,
            "aud": RoleEnum.UBERADMIN.value,
        },
        key=TEST_JWT_SECRET,
        algorithm="HS256",
    )


@pytest.mark.asyncio
async def test_login_tokens_include_original_session_started_at(
    session: AsyncSession, team_user, running_contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "JWT_EXPIRE_SECONDS", 600)
    _app, auth_service = _build_auth_app(session)

    token = await auth_service.user_login(
        username=team_user.username,
        password="TestPass1!",
        contest_id=running_contest.id,
        session=session,
    )

    result = auth_service.jwt_service.validate(token)
    assert result.valid is True
    assert result.extra_data is not None
    assert result.extra_data["contest_id"] == running_contest.id
    assert isinstance(result.extra_data["session_started_at"], int)
    history = (await session.execute(select(Login_History).where(Login_History.user_id == team_user.id))).scalar_one()
    assert isinstance(history.id, int)


@pytest.mark.asyncio
async def test_authenticated_request_outside_half_life_does_not_refresh_cookie(
    session: AsyncSession, team_user, running_contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "JWT_EXPIRE_SECONDS", 10)
    monkeypatch.setattr(settings, "JWT_REFRESH_MAX_SESSION_SECONDS", 0)
    await session.commit()
    app, auth_service = _build_auth_app(session)
    token = _build_contest_token(
        auth_service,
        username=team_user.username,
        contest_id=running_contest.id,
        expires_in=8,
        session_started_at=1_700_000_000,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get("/protected", follow_redirects=True)

    assert response.status_code == 200
    assert _extract_cookie_value(response, "noca_access_token") is None


@pytest.mark.asyncio
async def test_authenticated_request_inside_half_life_refreshes_cookie_and_preserves_claims(
    session: AsyncSession, team_user, running_contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "JWT_EXPIRE_SECONDS", 10)
    monkeypatch.setattr(settings, "JWT_REFRESH_MAX_SESSION_SECONDS", 0)
    await session.commit()
    app, auth_service = _build_auth_app(session)
    token = _build_contest_token(
        auth_service,
        username=team_user.username,
        contest_id=running_contest.id,
        expires_in=4,
        session_started_at=1_700_000_123,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get("/protected")

    assert response.status_code == 200
    refreshed_token = _extract_cookie_value(response, "noca_access_token")
    assert refreshed_token is not None
    refreshed = auth_service.jwt_service.validate(refreshed_token)
    assert refreshed.valid is True
    assert refreshed.sub == team_user.username
    assert refreshed.aud == RoleEnum.TEAM.value
    assert refreshed.action == AuthAction.WEB_ACCESS
    assert refreshed.extra_data == {
        "contest_id": running_contest.id,
        "session_started_at": 1_700_000_123,
    }
    assert refreshed.expires_in is not None
    assert refreshed.expires_in > 4


@pytest.mark.asyncio
async def test_semantically_invalid_token_is_not_refreshed(
    session: AsyncSession, team_user, running_contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "JWT_EXPIRE_SECONDS", 10)
    monkeypatch.setattr(settings, "JWT_REFRESH_MAX_SESSION_SECONDS", 0)
    await session.commit()
    app, auth_service = _build_auth_app(session)
    token = _build_contest_token(
        auth_service,
        username=team_user.username,
        contest_id="wrong-contest-id",
        expires_in=4,
        session_started_at=1_700_000_123,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get("/protected", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"
    assert _extract_cookie_value(response, "noca_access_token") is None


@pytest.mark.asyncio
async def test_expired_token_expires_cookie(
    session: AsyncSession, team_user, running_contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "JWT_EXPIRE_SECONDS", 10)
    monkeypatch.setattr(settings, "JWT_REFRESH_MAX_SESSION_SECONDS", 0)
    await session.commit()
    app, _auth_service = _build_auth_app(session)
    token = _build_expired_contest_token(
        username=team_user.username,
        contest_id=running_contest.id,
        session_started_at=1_700_000_123,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get("/protected", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == f"/c/{running_contest.login_slug}/login"
    deleted_cookie_header = response.headers.get("set-cookie", "")
    assert "noca_access_token=" in deleted_cookie_header
    assert "Max-Age=0" in deleted_cookie_header


@pytest.mark.asyncio
async def test_expired_contest_token_redirects_to_contest_login_with_flash(
    session: AsyncSession, team_user, running_contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "JWT_EXPIRE_SECONDS", 10)
    monkeypatch.setattr(settings, "JWT_REFRESH_MAX_SESSION_SECONDS", 0)
    await session.commit()
    app, _auth_service = _build_auth_app(session)
    token = _build_expired_contest_token(
        username=team_user.username,
        contest_id=running_contest.id,
        session_started_at=1_700_000_123,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get("/protected", follow_redirects=True)

    assert response.status_code == 200
    assert response.json() == {
        "slug": running_contest.login_slug,
        "messages": [["warning", SESSION_EXPIRED_MESSAGE]],
    }


@pytest.mark.asyncio
async def test_expired_uberadmin_token_redirects_to_uberadmin_login_with_flash(
    session: AsyncSession, uberadmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "JWT_EXPIRE_SECONDS", 10)
    monkeypatch.setattr(settings, "JWT_REFRESH_MAX_SESSION_SECONDS", 0)
    await session.commit()
    app, _auth_service = _build_auth_app(session)
    token = _build_expired_uberadmin_token(username=uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get("/uberprotected", follow_redirects=True)

    assert response.status_code == 200
    assert response.json() == {"messages": [["warning", SESSION_EXPIRED_MESSAGE]]}


@pytest.mark.asyncio
async def test_absolute_session_cap_blocks_access_and_expires_cookie(
    session: AsyncSession, team_user, running_contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "JWT_EXPIRE_SECONDS", 10)
    monkeypatch.setattr(settings, "JWT_REFRESH_MAX_SESSION_SECONDS", 60)
    await session.commit()
    app, auth_service = _build_auth_app(session)
    current_time = 1_700_001_000
    token = _build_contest_token(
        auth_service,
        username=team_user.username,
        contest_id=running_contest.id,
        expires_in=4,
        session_started_at=current_time - 120,
    )
    monkeypatch.setattr(auth_service, "_now_epoch_seconds", lambda: current_time)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get("/protected", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"
    deleted_cookie_header = response.headers.get("set-cookie", "")
    assert "noca_access_token=" in deleted_cookie_header
    assert "Max-Age=0" in deleted_cookie_header


@pytest.mark.asyncio
async def test_legacy_token_without_session_started_at_is_not_refreshed_when_cap_enabled(
    session: AsyncSession, team_user, running_contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "JWT_EXPIRE_SECONDS", 10)
    monkeypatch.setattr(settings, "JWT_REFRESH_MAX_SESSION_SECONDS", 60)
    await session.commit()
    app, auth_service = _build_auth_app(session)
    token = _build_contest_token(
        auth_service,
        username=team_user.username,
        contest_id=running_contest.id,
        expires_in=4,
        session_started_at=None,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get("/protected")

    assert response.status_code == 200
    assert _extract_cookie_value(response, "noca_access_token") is None
