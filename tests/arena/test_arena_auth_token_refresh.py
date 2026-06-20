#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for remembered Arena session token refresh."""

import logging
import time
import uuid
from datetime import date
from http.cookies import SimpleCookie
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_users  # noqa: F401
from arena.config import settings
from arena.dependencies.auth import get_current_arena_user
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_users import ArenaUser
from arena.services import session_service
from arena.services.token_service import ArenaTokenAction
from shared.enumerations import ArenaRole
from shared.services.email_service import EmailConfig, EmailService
from shared.services.imageprocessing_service import ImageProcessingConfig, ImageProcessingService

_TEST_JWT_SECRET = "test-secret-key-for-arena-refresh-tests-only!"


def _extract_cookie_value(response, name: str) -> str | None:
    """Return one cookie value from the response `Set-Cookie` headers."""
    cookie = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookie.load(header)
    morsel = cookie.get(name)
    return morsel.value if morsel else None


def _build_arena_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena app with the real middleware chain."""
    app = FastAPI()
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    arena_dir = Path(__file__).resolve().parents[2] / "arena"
    templates = Jinja2Templates(directory=arena_dir / "template")
    templates.env.globals["app_version"] = "test"
    templates.env.globals["next_rating_update_text"] = lambda request: None
    setup_flash(templates)

    app.state.arena_templates = templates
    app.state.arena_db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.state.jwt_service = JWTService(
        config=load_token_config_from_dict(
            {
                "SECRET_KEY": _TEST_JWT_SECRET,
                "JWTSERVICE_ALGORITHM": "HS256",
                "JWTSERVICE_ISSUER": "noca-arena-test",
            }
        ),
        logger=logging.getLogger(__name__),
        action_enum=ArenaTokenAction,
    )
    app.state.email_service = EmailService(
        config=EmailConfig(
            send_email=False,
            provider_type="mock",
            default_from_email="no-reply@test.example.com",
            default_from_name="NOCA Test",
            smtp_server=None,
            smtp_port=587,
            smtp_username=None,
            smtp_password=None,
            smtp_use_tls=True,
        ),
        logger=logging.getLogger(__name__),
    )
    app.state.image_service = ImageProcessingService(
        config=ImageProcessingConfig(max_file_size=2 * 1024 * 1024),
        logger=logging.getLogger(__name__),
    )

    @app.get("/auth/login", name="arena_login")
    async def _stub_login(request: Request) -> HTMLResponse:
        """Stub login page."""
        return HTMLResponse("login")

    @app.get("/protected", response_class=HTMLResponse)
    async def _protected(
        request: Request,
        current_user: ArenaUser | None = Depends(get_current_arena_user),
    ) -> Response:
        """Protected route that resolves a real Arena user before rendering."""
        if current_user is None:
            return RedirectResponse(url=str(request.url_for("arena_login")), status_code=303)
        return HTMLResponse(current_user.id)

    app.mount("/static/css", StaticFiles(directory=arena_dir / "static" / "css"), name="arena_static_css")
    app.mount("/static/img", StaticFiles(directory=arena_dir / "static" / "img"), name="arena_static_img")
    return app


async def _create_active_user(session: AsyncSession) -> ArenaUser:
    """Create an active Arena user for auth-refresh tests."""
    user = ArenaUser(
        id=str(uuid.uuid4()),
        nome="Refresh User",
        email_normalizado="refresh@test.example",
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        com_foto=False,
        usa_2fa=False,
        precisa_trocar_senha=False,
        session_version=1,
    )
    user.password = "StrongPass1!"
    session.add(user)
    await session.flush()
    return user


def _build_login_token(
    app: FastAPI,
    user: ArenaUser,
    *,
    expires_in: int,
    remember_me: bool,
    session_started_at: int | None = None,
) -> str:
    """Build a LOGIN token with explicit test-controlled claims."""
    extra_data: dict[str, object] = {"tid": user.get_token_id(), "remember_me": remember_me}
    if session_started_at is not None:
        extra_data["session_started_at"] = session_started_at
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=expires_in,
            extra_data=extra_data,
        )
    )


@pytest.mark.asyncio
async def test_remembered_request_inside_half_life_refreshes_cookie_and_preserves_claims(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remembered Arena sessions rotate near half-life and keep the original start timestamp."""
    monkeypatch.setattr(settings, "JWT_EXPIRE_SECONDS", 10)
    current_time = int(time.time())
    monkeypatch.setattr(session_service, "_now_epoch_seconds", lambda: current_time)
    app = _build_arena_app(session)
    user = await _create_active_user(session)
    await session.commit()
    session_started_at = current_time - 5
    token = _build_login_token(
        app,
        user,
        expires_in=4,
        remember_me=True,
        session_started_at=session_started_at,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/protected")

    assert response.status_code == 200
    refreshed_token = _extract_cookie_value(response, "arena_access_token")
    assert refreshed_token is not None
    refreshed = app.state.jwt_service.validar(refreshed_token)
    assert refreshed.valid is True
    assert refreshed.extra_data == {
        "tid": user.get_token_id(),
        "remember_me": True,
        "session_started_at": session_started_at,
    }
    assert refreshed.expires_in is not None
    assert refreshed.expires_in > 4
    cookie_header = response.headers.get("set-cookie", "")
    assert "Max-Age=2592000" in cookie_header


@pytest.mark.asyncio
async def test_non_remembered_request_inside_half_life_does_not_refresh_cookie(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regular Arena sessions stay fixed-lifetime and are not rotated by the middleware."""
    monkeypatch.setattr(settings, "JWT_EXPIRE_SECONDS", 10)
    app = _build_arena_app(session)
    user = await _create_active_user(session)
    await session.commit()
    token = _build_login_token(app, user, expires_in=4, remember_me=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/protected")

    assert response.status_code == 200
    assert _extract_cookie_value(response, "arena_access_token") is None


@pytest.mark.asyncio
async def test_remembered_request_past_absolute_cap_redirects_and_clears_cookie(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remembered Arena sessions expire once the 30-day absolute cap is exceeded."""
    monkeypatch.setattr(settings, "JWT_EXPIRE_SECONDS", 10)
    current_time = 1_700_100_000
    monkeypatch.setattr(session_service, "_now_epoch_seconds", lambda: current_time)
    app = _build_arena_app(session)
    user = await _create_active_user(session)
    await session.commit()
    token = _build_login_token(
        app,
        user,
        expires_in=4,
        remember_me=True,
        session_started_at=current_time - session_service.ARENA_REMEMBER_ME_MAX_AGE - 1,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/protected", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth/login")
    deleted_cookie_header = response.headers.get("set-cookie", "")
    assert "arena_access_token=" in deleted_cookie_header
    assert "Max-Age=0" in deleted_cookie_header
