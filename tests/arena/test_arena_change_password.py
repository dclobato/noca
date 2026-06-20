#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the Arena forced-password-change flow.

Covers GET and POST /auth/change-password:

- GET without ``pending_pw_change_token`` in session → redirect to login (303)
- GET with a valid session token → renders form (200)
- POST without session token → redirect to login
- POST with wrong current password → redirect back to change-password, flag unchanged
- POST with mismatched passwords → redirect back to change-password
- POST with valid data, user without 2FA → LOGIN cookie set, redirect to dashboard,
  ``precisa_trocar_senha`` cleared
- POST with valid data, user with 2FA → LOGIN cookie set, redirect to dashboard
  (2FA was already cleared before the forced-password-change flow runs)
"""

import logging
import time
import uuid
from datetime import date
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_users  # noqa: F401
from arena.config import settings
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_users import ArenaUser
from arena.routes.auth import router as arena_auth_router
from arena.routes.auth_password import router as arena_auth_password_router
from arena.services.arena_auth_service import set_pending_password_change_token
from arena.services.token_service import ArenaTokenAction
from shared.enumerations import ArenaRole
from shared.services.email_service import EmailConfig, EmailService
from shared.services.imageprocessing_service import ImageProcessingConfig, ImageProcessingService

_TEST_JWT_SECRET = "test-secret-key-for-arena-pw-change-tests!32bytes"
_DEFAULT_LOGIN_SECONDS = 3600
_REMEMBER_ME_SECONDS = 30 * 24 * 3600


def _build_arena_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena FastAPI app for the change-password route tests.

    Registers stub endpoints for all named routes referenced in redirects
    (``arena_dashboard``, ``arena_login``, ``arena_change_password``, ``arena_2fa``).

    Args:
        session: Async database session used to back the session factory.

    Returns:
        FastAPI: Minimal application suitable for change-password testing.
    """
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
    app.state.geo_service = None

    stubs = APIRouter()

    @stubs.get("/dashboard", name="arena_dashboard")
    async def _stub_dashboard(request: Request) -> HTMLResponse:
        """Stub dashboard endpoint."""
        return HTMLResponse("dashboard")

    @stubs.get("/user/profile/complete", name="arena_user_profile_completion")
    async def _stub_profile_completion(request: Request) -> HTMLResponse:
        """Stub profile completion endpoint."""
        return HTMLResponse("profile completion")

    @stubs.get("/status", name="arena_status")
    async def _stub_status(request: Request) -> HTMLResponse:
        """Stub status endpoint."""
        return HTMLResponse("status")

    @stubs.get("/auth/login", name="arena_login")
    async def _stub_login(request: Request) -> HTMLResponse:
        """Stub login page."""
        return HTMLResponse("login")

    @stubs.get("/auth/2fa", name="arena_2fa")
    async def _stub_2fa(request: Request) -> HTMLResponse:
        """Stub 2FA entry page."""
        return HTMLResponse("two_factor")

    @stubs.get("/classes", name="arena_classes_index")
    async def _stub_classes_index(request: Request) -> HTMLResponse:
        """Stub class list endpoint."""
        return HTMLResponse("classes")

    @stubs.get("/classes/registered", name="arena_classes_registered")
    async def _stub_classes_registered(request: Request) -> HTMLResponse:
        return HTMLResponse("classes registered")

    @stubs.get("/classes/open", name="arena_classes_open")
    async def _stub_classes_open(request: Request) -> HTMLResponse:
        return HTMLResponse("classes open")

    @stubs.get("/classes/manage", name="arena_classes_manage")
    async def _stub_classes_manage(request: Request) -> HTMLResponse:
        return HTMLResponse("classes manage")

    @stubs.get("/image/users/{user_id}/avatar", name="arena_user_avatar_by_id")
    async def _stub_avatar(user_id: str) -> HTMLResponse:
        """Stub avatar endpoint."""
        return HTMLResponse("avatar")

    @stubs.get("/user/profile", name="arena_user_profile")
    async def _stub_profile(request: Request) -> HTMLResponse:
        """Stub profile page."""
        return HTMLResponse("profile")

    app.include_router(stubs)
    shared_dir = arena_dir.parent / "shared"
    app.mount("/static/css", StaticFiles(directory=arena_dir / "static" / "css"), name="arena_static_css")
    app.mount("/static/js", StaticFiles(directory=arena_dir / "static" / "js"), name="arena_static_js")
    app.mount("/static/img", StaticFiles(directory=arena_dir / "static" / "img"), name="arena_static_img")
    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/shared-js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")
    app.include_router(arena_auth_router)
    app.include_router(arena_auth_password_router)
    return app


async def _create_user_requiring_pw_change(
    session: AsyncSession,
    email: str = "pwchange@test.example",
    usa_2fa: bool = False,
    *,
    complete_profile: bool = True,
) -> ArenaUser:
    """Create an active Arena user with ``precisa_trocar_senha=True``.

    Args:
        session: Async database session.
        email: Normalised email to assign.
        usa_2fa: Whether 2FA is enabled for the user.

    Returns:
        ArenaUser: Persisted user ready for forced-password-change flow tests.
    """
    user = ArenaUser(
        id=str(uuid.uuid4()),
        nome="PW Change User",
        email_normalizado=email,
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        com_foto=False,
        usa_2fa=usa_2fa,
        precisa_trocar_senha=True,
        session_version=1,
        affiliation_id="test-affiliation" if complete_profile else None,
        preferred_language_id="python" if complete_profile else None,
        country_code="BR" if complete_profile else None,
        prefered_language="en-US",
    )
    user.password = "OldPass1!"
    # Manually re-set the flag since the password setter clears it.
    user.precisa_trocar_senha = True
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_forced_password_change_incomplete_profile_overrides_safe_next(
    session: AsyncSession,
) -> None:
    """Completed forced password change prioritizes profile completion."""
    app = _build_arena_app(session)
    user = await _create_user_requiring_pw_change(session, complete_profile=False)
    await session.commit()
    pending_token = set_pending_password_change_token(
        user,
        app.state.jwt_service,
        next_page="/submissions/sub-1",
    )
    _add_session_writer(app, pending_token, "/test-set-pw-incomplete")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post("/test-set-pw-incomplete")
        response = await client.post(
            "/auth/change-password",
            data={
                "current_password": "OldPass1!",
                "new_password": "FreshNewPass1!",
                "confirm_password": "FreshNewPass1!",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"].endswith("/user/profile/complete")


# ---------------------------------------------------------------------------
# Helper: session-injection endpoint factory
# ---------------------------------------------------------------------------


def _add_session_writer(app: FastAPI, token: str, endpoint_path: str) -> None:
    """Register a POST stub that writes ``pending_pw_change_token`` to the session.

    Args:
        app: FastAPI application to register the route on.
        token: The PENDING_PASSWORD_CHANGE JWT to store in the session.
        endpoint_path: URL path for the injector endpoint.
    """

    @app.post(endpoint_path)
    async def _writer(request: Request) -> HTMLResponse:
        request.session["pending_pw_change_token"] = token
        return HTMLResponse("ok")


# ---------------------------------------------------------------------------
# GET /auth/change-password
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_change_password_without_session_token_redirects_to_login(
    session: AsyncSession,
) -> None:
    """GET /auth/change-password with no session token redirects to login."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/change-password", follow_redirects=False)

    assert response.status_code == 303
    assert "/auth/login" in response.headers["location"]


@pytest.mark.asyncio
async def test_get_change_password_with_valid_token_renders_form(session: AsyncSession) -> None:
    """GET /auth/change-password with a valid session token returns 200 with the form."""
    app = _build_arena_app(session)
    user = await _create_user_requiring_pw_change(session)
    await session.commit()

    pending_token = set_pending_password_change_token(user, app.state.jwt_service)
    _add_session_writer(app, pending_token, "/test-set-pw-change-token")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post("/test-set-pw-change-token")
        response = await client.get("/auth/change-password", follow_redirects=False)

    assert response.status_code == 200
    # The template should contain a password input field.
    assert "password" in response.text.lower()


# ---------------------------------------------------------------------------
# POST /auth/change-password
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_change_password_without_session_token_redirects_to_login(
    session: AsyncSession,
) -> None:
    """POST /auth/change-password with no session token redirects to login."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/change-password",
            data={"new_password": "NewPass1!", "confirm_password": "NewPass1!"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "/auth/login" in response.headers["location"]
    assert "arena_access_token" not in response.cookies


@pytest.mark.asyncio
async def test_post_change_password_wrong_current_password_redirects_back(
    session: AsyncSession,
) -> None:
    """POST /auth/change-password with wrong current password redirects back to the form."""
    app = _build_arena_app(session)
    user = await _create_user_requiring_pw_change(session)
    await session.commit()

    pending_token = set_pending_password_change_token(user, app.state.jwt_service)
    _add_session_writer(app, pending_token, "/test-set-pw-wrong-current")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post("/test-set-pw-wrong-current")
        response = await client.post(
            "/auth/change-password",
            data={
                "current_password": "WrongPassword!",
                "new_password": "NewPass1!",
                "confirm_password": "NewPass1!",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "/auth/change-password" in response.headers["location"]
    assert "arena_access_token" not in response.cookies


@pytest.mark.asyncio
async def test_post_change_password_with_mismatched_passwords_redirects_back(
    session: AsyncSession,
) -> None:
    """POST /auth/change-password with mismatched passwords redirects back to the form."""
    app = _build_arena_app(session)
    user = await _create_user_requiring_pw_change(session)
    await session.commit()

    pending_token = set_pending_password_change_token(user, app.state.jwt_service)
    _add_session_writer(app, pending_token, "/test-set-pw-mismatch")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post("/test-set-pw-mismatch")
        response = await client.post(
            "/auth/change-password",
            data={
                "current_password": "OldPass1!",
                "new_password": "NewPass1!",
                "confirm_password": "DifferentPass1!",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "/auth/change-password" in response.headers["location"]
    assert "arena_access_token" not in response.cookies


@pytest.mark.asyncio
async def test_post_change_password_no_2fa_sets_cookie_and_redirects_to_dashboard(
    session: AsyncSession,
) -> None:
    """POST /auth/change-password for a user without 2FA issues a LOGIN cookie and redirects."""
    app = _build_arena_app(session)
    user = await _create_user_requiring_pw_change(session, usa_2fa=False)
    await session.commit()

    pending_token = set_pending_password_change_token(user, app.state.jwt_service)
    _add_session_writer(app, pending_token, "/test-set-pw-no2fa")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post("/test-set-pw-no2fa")
        response = await client.post(
            "/auth/change-password",
            data={
                "current_password": "OldPass1!",
                "new_password": "FreshNewPass1!",
                "confirm_password": "FreshNewPass1!",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "/dashboard" in response.headers["location"]
    assert "arena_access_token" in response.cookies


@pytest.mark.asyncio
async def test_post_change_password_no_2fa_redirects_to_safe_next(
    session: AsyncSession,
) -> None:
    """Forced password change redirects to the safe next URL from the pending token."""
    app = _build_arena_app(session)
    user = await _create_user_requiring_pw_change(session, usa_2fa=False)
    await session.commit()

    pending_token = set_pending_password_change_token(
        user,
        app.state.jwt_service,
        next_page="/submissions/sub-1?source=queue",
    )
    _add_session_writer(app, pending_token, "/test-set-pw-next")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post("/test-set-pw-next")
        response = await client.post(
            "/auth/change-password",
            data={
                "current_password": "OldPass1!",
                "new_password": "FreshNewPass1!",
                "confirm_password": "FreshNewPass1!",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/submissions/sub-1?source=queue"
    assert "arena_access_token" in response.cookies


@pytest.mark.asyncio
async def test_post_change_password_no_2fa_clears_precisa_trocar_senha(
    session: AsyncSession,
) -> None:
    """Successful password change must clear the ``precisa_trocar_senha`` flag."""
    app = _build_arena_app(session)
    user = await _create_user_requiring_pw_change(session, usa_2fa=False)
    await session.commit()
    user_id = user.id

    pending_token = set_pending_password_change_token(user, app.state.jwt_service)
    _add_session_writer(app, pending_token, "/test-set-pw-clear-flag")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post("/test-set-pw-clear-flag")
        await client.post(
            "/auth/change-password",
            data={
                "current_password": "OldPass1!",
                "new_password": "FreshNewPass1!",
                "confirm_password": "FreshNewPass1!",
            },
            follow_redirects=False,
        )

    # Expire the session identity map so the next query hits the database rather than
    # returning the cached pre-change object.
    from sqlalchemy import select

    session.expire_all()
    result = await session.execute(select(ArenaUser).where(ArenaUser.id == user_id))
    updated_user = result.scalar_one_or_none()
    assert updated_user is not None
    assert updated_user.precisa_trocar_senha is False


@pytest.mark.asyncio
async def test_post_change_password_no_2fa_token_action_is_login(
    session: AsyncSession,
) -> None:
    """The issued JWT after a successful password change must have action=LOGIN."""
    app = _build_arena_app(session)
    user = await _create_user_requiring_pw_change(session, usa_2fa=False)
    await session.commit()

    pending_token = set_pending_password_change_token(user, app.state.jwt_service)
    _add_session_writer(app, pending_token, "/test-set-pw-jwt-action")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post("/test-set-pw-jwt-action")
        response = await client.post(
            "/auth/change-password",
            data={
                "current_password": "OldPass1!",
                "new_password": "FreshNewPass1!",
                "confirm_password": "FreshNewPass1!",
            },
            follow_redirects=False,
        )

    raw_token = response.cookies["arena_access_token"]
    claims = app.state.jwt_service.validar(raw_token)
    assert claims.valid
    assert claims.action == ArenaTokenAction.LOGIN
    assert claims.sub == user.id


@pytest.mark.asyncio
async def test_post_change_password_with_2fa_issues_login_jwt_and_redirects_to_dashboard(
    session: AsyncSession,
) -> None:
    """POST /auth/change-password for a 2FA user issues a LOGIN cookie and redirects to dashboard.

    The forced-password-change token is only issued *after* 2FA has already been
    cleared (by ``arena_2fa_submit``), so no second 2FA prompt is needed here.
    """
    app = _build_arena_app(session)
    user = await _create_user_requiring_pw_change(session, usa_2fa=True)
    await session.commit()

    pending_token = set_pending_password_change_token(user, app.state.jwt_service)
    _add_session_writer(app, pending_token, "/test-set-pw-with2fa")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post("/test-set-pw-with2fa")
        response = await client.post(
            "/auth/change-password",
            data={
                "current_password": "OldPass1!",
                "new_password": "FreshNewPass1!",
                "confirm_password": "FreshNewPass1!",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "/dashboard" in response.headers["location"]
    # 2FA was already cleared before this route was reached — LOGIN cookie issued directly.
    assert "arena_access_token" in response.cookies


@pytest.mark.asyncio
async def test_post_change_password_with_2fa_issues_login_action_jwt(
    session: AsyncSession,
) -> None:
    """The JWT issued after password change for a 2FA user must have action=LOGIN."""
    app = _build_arena_app(session)
    user = await _create_user_requiring_pw_change(session, usa_2fa=True)
    await session.commit()

    pending_token = set_pending_password_change_token(user, app.state.jwt_service)
    _add_session_writer(app, pending_token, "/test-set-pw-2fa-jwt-check")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post("/test-set-pw-2fa-jwt-check")
        response = await client.post(
            "/auth/change-password",
            data={
                "current_password": "OldPass1!",
                "new_password": "FreshNewPass1!",
                "confirm_password": "FreshNewPass1!",
            },
            follow_redirects=False,
        )

    raw_token = response.cookies["arena_access_token"]
    claims = app.state.jwt_service.validar(raw_token)
    assert claims.valid
    assert claims.action == ArenaTokenAction.LOGIN
    assert claims.sub == user.id


@pytest.mark.asyncio
async def test_post_change_password_with_remember_me_sets_max_age(
    session: AsyncSession,
) -> None:
    """Forced remembered password changes keep cookie persistence and remember-me metadata."""
    app = _build_arena_app(session)
    user = await _create_user_requiring_pw_change(session, usa_2fa=False)
    await session.commit()

    pending_token = set_pending_password_change_token(user, app.state.jwt_service, remember_me=True)
    _add_session_writer(app, pending_token, "/test-set-pw-remember")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post("/test-set-pw-remember")
        response = await client.post(
            "/auth/change-password",
            data={
                "current_password": "OldPass1!",
                "new_password": "FreshNewPass1!",
                "confirm_password": "FreshNewPass1!",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "arena_access_token" in response.cookies
    cookie_header = response.headers.get("set-cookie", "")
    assert "max-age=" in cookie_header.lower()
    claims = app.state.jwt_service.validar(response.cookies["arena_access_token"])
    assert claims.valid
    assert claims.expires_in is not None
    assert _DEFAULT_LOGIN_SECONDS - 5 <= claims.expires_in <= _DEFAULT_LOGIN_SECONDS
    assert claims.extra_data is not None
    assert claims.extra_data.get("remember_me") is True
    assert isinstance(claims.extra_data.get("session_started_at"), int)


# ---------------------------------------------------------------------------
# Voluntary password change (authenticated user, no pending token)
# ---------------------------------------------------------------------------


async def _create_active_user(
    session: AsyncSession,
    email: str = "active@test.example",
) -> ArenaUser:
    """Create an active Arena user with no forced-password-change flag.

    Args:
        session: Async database session.
        email: Normalised email to assign.

    Returns:
        ArenaUser: Persisted user ready for voluntary password-change tests.
    """
    user = ArenaUser(
        id=str(uuid.uuid4()),
        nome="Active User",
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
    user.password = "CurrentPass1!"
    session.add(user)
    await session.flush()
    return user


def _issue_login_token(
    app: FastAPI,
    user: ArenaUser,
    *,
    remember_me: bool = False,
    session_started_at: int | None = None,
) -> str:
    """Issue a valid Arena LOGIN JWT for the given user.

    Args:
        app: FastAPI application whose jwt_service to use.
        user: The authenticated Arena user.

    Returns:
        str: Signed JWT.
    """
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=_DEFAULT_LOGIN_SECONDS,
            extra_data={
                "tid": user.get_token_id(),
                "remember_me": remember_me,
                **({"session_started_at": session_started_at or 1_700_000_123} if remember_me else {}),
            },
        )
    )


@pytest.mark.asyncio
async def test_voluntary_change_password_redirects_to_profile(session: AsyncSession) -> None:
    """Voluntary password change redirects to the profile page on success."""
    app = _build_arena_app(session)
    user = await _create_active_user(session)
    await session.commit()
    token = _issue_login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.post(
            "/auth/change-password",
            data={
                "current_password": "CurrentPass1!",
                "new_password": "BrandNewPass1!",
                "confirm_password": "BrandNewPass1!",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "/user/profile" in response.headers["location"]


@pytest.mark.asyncio
async def test_voluntary_change_password_refreshes_jwt_cookie(session: AsyncSession) -> None:
    """Voluntary password change must refresh the JWT cookie so the session stays active.

    After the password setter increments session_version, the old JWT tid no longer
    matches get_token_id().  A new cookie must be issued to prevent a force-logout
    on the very next request.
    """
    app = _build_arena_app(session)
    user = await _create_active_user(session)
    await session.commit()
    old_token = _issue_login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", old_token)
        response = await client.post(
            "/auth/change-password",
            data={
                "current_password": "CurrentPass1!",
                "new_password": "BrandNewPass1!",
                "confirm_password": "BrandNewPass1!",
            },
            follow_redirects=False,
        )

    # A new cookie must be set — without this the next request force-logs the user out.
    assert "arena_access_token" in response.cookies
    new_token = response.cookies["arena_access_token"]
    assert new_token != old_token

    claims = app.state.jwt_service.validar(new_token)
    assert claims.valid
    assert claims.action == ArenaTokenAction.LOGIN
    assert claims.sub == user.id


@pytest.mark.asyncio
async def test_voluntary_change_password_preserves_remember_me_session(session: AsyncSession) -> None:
    """Voluntary password changes keep remembered Arena sessions persistent."""
    app = _build_arena_app(session)
    user = await _create_active_user(session, email="remembered@test.example")
    await session.commit()
    session_started_at = int(time.time())
    old_token = _issue_login_token(app, user, remember_me=True, session_started_at=session_started_at)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", old_token)
        response = await client.post(
            "/auth/change-password",
            data={
                "current_password": "CurrentPass1!",
                "new_password": "BrandNewPass1!",
                "confirm_password": "BrandNewPass1!",
            },
            follow_redirects=False,
        )

    cookie_header = response.headers.get("set-cookie", "")
    assert "max-age=" in cookie_header.lower()
    new_token = response.cookies["arena_access_token"]
    claims = app.state.jwt_service.validar(new_token)
    assert claims.valid
    assert claims.expires_in is not None
    assert _DEFAULT_LOGIN_SECONDS - 5 <= claims.expires_in <= _DEFAULT_LOGIN_SECONDS
    assert claims.extra_data is not None
    assert claims.extra_data.get("remember_me") is True
    assert claims.extra_data.get("session_started_at") == session_started_at


@pytest.mark.asyncio
async def test_voluntary_change_password_does_not_append_duplicate_refresh_cookie(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Password-change responses must own the cookie without middleware adding a second one."""
    monkeypatch.setattr(settings, "JWT_EXPIRE_SECONDS", 10)
    app = _build_arena_app(session)
    user = await _create_active_user(session, email="half-life@test.example")
    await session.commit()
    old_token = str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=4,
            extra_data={
                "tid": user.get_token_id(),
                "remember_me": True,
                "session_started_at": int(time.time()),
            },
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", old_token)
        response = await client.post(
            "/auth/change-password",
            data={
                "current_password": "CurrentPass1!",
                "new_password": "BrandNewPass1!",
                "confirm_password": "BrandNewPass1!",
            },
            follow_redirects=False,
        )

    auth_cookie_headers = [
        header for header in response.headers.get_list("set-cookie") if header.startswith("arena_access_token=")
    ]
    assert len(auth_cookie_headers) == 1
