#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for ArenaAuthMiddleware and the get_current_arena_user dependency.

Covers:
  - Middleware: no cookie, valid LOGIN token, invalid token, wrong-action token.
  - Dependency: no session, valid session + matching tid, missing tid claim,
    unknown user id, stale tid (force logout).
"""

import logging
import uuid
from datetime import date
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse, RedirectResponse

import arena.models.arena_users  # noqa: F401
from arena.dependencies.auth import ForceLogoutException, get_current_arena_user
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_users import ArenaUser
from arena.services.token_service import ArenaTokenAction
from shared.enumerations import ArenaRole
from shared.services.email_service import EmailConfig, EmailService
from shared.services.imageprocessing_service import ImageProcessingConfig, ImageProcessingService

_TEST_JWT_SECRET = "test-secret-key-for-arena-tests-only-32bytes"


def _build_jwt_service() -> JWTService:
    """Create a JWTService configured for arena tests.

    Returns:
        JWTService: Test-scoped JWT service instance.
    """
    return JWTService(
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


def _build_full_arena_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena app with the full auth middleware chain.

    Includes ``ArenaAuthMiddleware``, ``SessionMiddleware``, and the
    ``ForceLogoutException`` handler — matching the production setup in
    ``arena/main.py``.  Two probe routes allow tests to inspect middleware
    state and dependency resolution without requiring real templates.

    Args:
        session: Async database session to bind the session factory to.

    Returns:
        FastAPI: Fully configured test application.
    """
    app = FastAPI()

    # Middleware order mirrors arena/main.py:
    #   - registered first = innermost (closest to the route)
    #   - registered last  = outermost (processes request first)
    # ArenaAuthMiddleware is registered first (inner), SessionMiddleware last (outer).
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    arena_dir = Path(__file__).resolve().parents[2] / "arena"
    templates = Jinja2Templates(directory=arena_dir / "template")
    templates.env.globals["app_version"] = "test"
    setup_flash(templates)

    app.state.arena_templates = templates
    app.state.arena_db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.state.jwt_service = _build_jwt_service()
    app.state.geo_service = None
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

    @app.exception_handler(ForceLogoutException)
    async def force_logout_handler(request: Request, exc: ForceLogoutException) -> RedirectResponse:
        """Redirect to login and clear the stale cookie on force logout."""
        response = RedirectResponse(url=str(request.url_for("arena_login")), status_code=303)
        response.delete_cookie("arena_access_token", httponly=True, samesite="lax")
        return response

    # Probe route: reports whether the middleware populated validated_token.
    @app.get("/probe/token-state", name="probe_token_state")
    async def probe_token_state(request: Request) -> JSONResponse:
        """Return whether the middleware set a non-None validated_token."""
        token = getattr(request.state, "validated_token", "NOT_SET")
        return JSONResponse({"has_token": token is not None, "is_set": token != "NOT_SET"})

    # Probe route: resolves current user via the FastAPI dependency.
    @app.get("/probe/current-user", name="probe_current_user")
    async def probe_current_user(
        request: Request,
        current_user: ArenaUser | None = Depends(get_current_arena_user),
    ) -> JSONResponse:
        """Return the authenticated user's id, or null for guests."""
        return JSONResponse({"user_id": current_user.id if current_user else None})

    # Named stub routes referenced by url_for in the exception handler.
    @app.get("/auth/login", name="arena_login")
    async def stub_login(request: Request) -> HTMLResponse:
        """Stub login page for url_for resolution."""
        return HTMLResponse("login")

    app.mount(
        "/static/css",
        StaticFiles(directory=arena_dir / "static" / "css"),
        name="arena_static_css",
    )
    app.mount(
        "/static/img",
        StaticFiles(directory=arena_dir / "static" / "img"),
        name="arena_static_img",
    )

    return app


async def _create_active_user(
    session: AsyncSession,
    email: str = "user@test.example",
    password: str = "StrongPass1!",
) -> ArenaUser:
    """Persist a fully active and email-confirmed ArenaUser.

    Args:
        session: Async database session.
        email: Normalised email address.
        password: Plaintext password (stored as hash).

    Returns:
        ArenaUser: The flushed user instance.
    """
    user = ArenaUser(
        id=str(uuid.uuid4()),
        nome="Test User",
        email_normalizado=email,
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
    user.password = password
    session.add(user)
    await session.flush()
    return user


# ---------------------------------------------------------------------------
# ArenaAuthMiddleware tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_no_cookie_sets_validated_token_to_none(session: AsyncSession) -> None:
    """Without an arena_access_token cookie validated_token is None in request.state."""
    app = _build_full_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/probe/token-state")

    assert response.status_code == 200
    data = response.json()
    assert data["is_set"] is True
    assert data["has_token"] is False


@pytest.mark.asyncio
async def test_middleware_valid_login_token_sets_validated_token(session: AsyncSession) -> None:
    """A valid LOGIN cookie makes the middleware store a non-None validated_token."""
    app = _build_full_arena_app(session)
    token = str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub="some-user-id",
            expires_in=3600,
            extra_data={"tid": "some-tid"},
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/probe/token-state")

    assert response.status_code == 200
    assert response.json()["has_token"] is True


@pytest.mark.asyncio
async def test_middleware_invalid_token_clears_stale_cookie(session: AsyncSession) -> None:
    """An invalid/malformed cookie triggers a delete-cookie Set-Cookie header."""
    app = _build_full_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", "totally.invalid.jwt")
        response = await client.get("/probe/token-state")

    assert response.json()["has_token"] is False
    assert "arena_access_token" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_middleware_wrong_action_token_clears_stale_cookie(session: AsyncSession) -> None:
    """A valid JWT with an action claim other than LOGIN is rejected and the cookie cleared."""
    app = _build_full_arena_app(session)
    wrong_action_token = str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.RESET_PASSWORD,
            sub="some-user-id",
            expires_in=3600,
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", wrong_action_token)
        response = await client.get("/probe/token-state")

    assert response.json()["has_token"] is False
    assert "arena_access_token" in response.headers.get("set-cookie", "")


# ---------------------------------------------------------------------------
# get_current_arena_user dependency tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dependency_returns_none_without_cookie(session: AsyncSession) -> None:
    """With no session cookie get_current_arena_user returns None."""
    app = _build_full_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/probe/current-user")

    assert response.status_code == 200
    assert response.json()["user_id"] is None


@pytest.mark.asyncio
async def test_dependency_returns_user_on_valid_session(session: AsyncSession) -> None:
    """A valid LOGIN token with a matching tid claim returns the authenticated ArenaUser."""
    app = _build_full_arena_app(session)
    user = await _create_active_user(session)
    await session.commit()

    token = str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/probe/current-user")

    assert response.status_code == 200
    assert response.json()["user_id"] == user.id


@pytest.mark.asyncio
async def test_dependency_returns_none_when_tid_missing(session: AsyncSession) -> None:
    """A LOGIN token without extra_data.tid is treated as invalid — returns None."""
    app = _build_full_arena_app(session)
    user = await _create_active_user(session)
    await session.commit()

    # Token has no extra_data / tid claim.
    token = str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/probe/current-user")

    assert response.status_code == 200
    assert response.json()["user_id"] is None


@pytest.mark.asyncio
async def test_dependency_returns_none_for_unknown_user(session: AsyncSession) -> None:
    """A LOGIN token whose sub references a non-existent user returns None."""
    app = _build_full_arena_app(session)
    nonexistent_id = str(uuid.uuid4())

    token = str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=nonexistent_id,
            expires_in=3600,
            extra_data={"tid": "any-tid-value"},
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/probe/current-user")

    assert response.status_code == 200
    assert response.json()["user_id"] is None


@pytest.mark.asyncio
async def test_dependency_stale_tid_triggers_force_logout_redirect(session: AsyncSession) -> None:
    """When tid no longer matches user.get_token_id() the handler redirects to login."""
    app = _build_full_arena_app(session)
    user = await _create_active_user(session)
    await session.commit()

    # Simulate a stale token issued before a password change.
    stale_tid = f"{user.id}|stale_hash_suffix_xx|0"
    token = str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": stale_tid},
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/probe/current-user", follow_redirects=False)

    # ForceLogoutException handler issues a 303 → login page.
    assert response.status_code == 303
    assert "/auth/login" in response.headers["location"]
    # Cookie must be deleted by the exception handler.
    assert "arena_access_token" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_dependency_force_logout_fires_after_password_change(session: AsyncSession) -> None:
    """After the user changes password (session_version incremented) old tokens are rejected."""
    app = _build_full_arena_app(session)
    user = await _create_active_user(session)
    old_tid = user.get_token_id()

    # Simulate a password change by incrementing session_version.
    user.password = "NewStrongPass2!"
    await session.flush()
    await session.commit()

    # Token was issued before the password change.
    token = str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": old_tid},
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/probe/current-user", follow_redirects=False)

    assert response.status_code == 303
    assert "/auth/login" in response.headers["location"]
