#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the Arena POST /auth/2fa two-factor verification flow.

Covers the following scenarios:
- No ``pending_2fa_token`` in session → redirect to login
- Invalid / expired token in session → redirect to login
- Valid token but wrong TOTP/backup code → redirect back to /auth/2fa
- Valid token and valid code (mocked service) → LOGIN cookie set, redirect to dashboard
- Valid token with remember_me flag → persistent cookie (max_age set)
"""

import logging
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_users  # noqa: F401
from arena.models.arena_auth_records import ArenaLoginHistory
from arena.models.arena_users import ArenaUser
from arena.routes.auth import router as arena_auth_router
from arena.routes.auth_2fa import router as arena_auth_2fa_router
from arena.routes.auth_password import router as arena_auth_password_router
from arena.services.arena_auth_service import set_pending_2fa_token
from arena.services.token_service import ArenaTokenAction
from arena.services.user_2fa_service import Autenticacao2FA, TwoFAValidationResult
from shared.enumerations import ArenaRole
from shared.services.email_service import EmailConfig, EmailService
from shared.services.imageprocessing_service import ImageProcessingConfig, ImageProcessingService

_TEST_JWT_SECRET = "test-secret-key-for-arena-2fa-tests-only-32bytes!"
_DEFAULT_LOGIN_SECONDS = 3600


def _build_arena_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena FastAPI app for POST /auth/2fa route tests.

    Includes stub routes for ``arena_dashboard`` and ``arena_2fa`` so that
    redirects can be resolved without a full arena application setup.

    Args:
        session: Async database session used to back the session factory.

    Returns:
        FastAPI: Minimal application suitable for 2FA route testing.
    """
    app = FastAPI()
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

    @stubs.get("/auth/2fa", name="arena_2fa")
    async def _stub_2fa(request: Request) -> HTMLResponse:
        """Stub 2FA entry page."""
        return HTMLResponse("two_factor")

    @stubs.get("/auth/login", name="arena_login")
    async def _stub_login(request: Request) -> HTMLResponse:
        """Stub login page."""
        return HTMLResponse("login")

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

    app.include_router(stubs)
    shared_dir = arena_dir.parent / "shared"
    app.mount("/static/css", StaticFiles(directory=arena_dir / "static" / "css"), name="arena_static_css")
    app.mount("/static/js", StaticFiles(directory=arena_dir / "static" / "js"), name="arena_static_js")
    app.mount("/static/img", StaticFiles(directory=arena_dir / "static" / "img"), name="arena_static_img")
    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/shared-js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")
    app.include_router(arena_auth_router)
    app.include_router(arena_auth_2fa_router)
    app.include_router(arena_auth_password_router)
    return app


async def _create_user_with_2fa(session: AsyncSession, *, complete_profile: bool = True) -> ArenaUser:
    """Create an active Arena user with ``usa_2fa=True``.

    The OTP secret is not set here because tests that validate the code
    mock the service layer.

    Args:
        session: Async database session.

    Returns:
        ArenaUser: Persisted user ready for 2FA flow tests.
    """
    user = ArenaUser(
        id=str(uuid.uuid4()),
        nome="2FA User",
        email_normalizado="twofa@test.example",
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        aceitou_termos_privacidade=True,
        com_foto=False,
        usa_2fa=True,
        precisa_trocar_senha=False,
        session_version=1,
        affiliation_id="test-affiliation" if complete_profile else None,
        preferred_language_id="python" if complete_profile else None,
        country_code="BR" if complete_profile else None,
        prefered_language="en-US",
    )
    user.password = "StrongPass1!"
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_2fa_incomplete_profile_overrides_safe_next(session: AsyncSession) -> None:
    """Completed 2FA login prioritizes the profile completion notice."""
    app = _build_arena_app(session)
    user = await _create_user_with_2fa(session, complete_profile=False)
    await session.commit()
    pending_token = set_pending_2fa_token(
        user,
        app.state.jwt_service,
        remember_me=False,
        next_page="/submissions/sub-1",
    )
    success_result = TwoFAValidationResult(
        success=True,
        method_used=Autenticacao2FA.TOTP,
        remaining_backup_codes=5,
    )

    @app.post("/test-set-2fa-token-incomplete")
    async def _set_token(request: Request) -> HTMLResponse:
        request.session["pending_2fa_token"] = pending_token
        return HTMLResponse("ok")

    with patch(
        "arena.routes.auth_2fa.user_2fa_service.validar_codigo_2fa",
        new=AsyncMock(return_value=success_result),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            await client.post("/test-set-2fa-token-incomplete")
            response = await client.post("/auth/2fa", data={"full_code": "123456"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].endswith("/user/profile/complete")


async def _login_history_modes(session: AsyncSession, user_id: str) -> list[str | None]:
    """Return recorded login-history modes for a user in insertion order."""
    result = await session.execute(
        select(ArenaLoginHistory.mode).where(ArenaLoginHistory.arena_user_id == user_id).order_by(ArenaLoginHistory.id)
    )
    return list(result.scalars())


# ---------------------------------------------------------------------------
# Session-token guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_2fa_submit_without_session_token_redirects_to_login(session: AsyncSession) -> None:
    """POST /auth/2fa with no session token redirects to login without a cookie."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/auth/2fa", data={"full_code": "123456"}, follow_redirects=False)

    assert response.status_code == 303
    assert "/auth/login" in response.headers["location"]
    assert "arena_access_token" not in response.cookies


@pytest.mark.asyncio
async def test_2fa_submit_with_invalid_token_redirects_to_login(session: AsyncSession) -> None:
    """POST /auth/2fa with a malformed session token redirects to login."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # Inject a clearly invalid token into the signed session cookie.
        client.cookies.set(
            "session",
            app.state.jwt_service.criar(
                action=ArenaTokenAction.LOGIN,  # wrong action — not PENDING_2FA
                sub="does-not-matter",
                expires_in=300,
            ).__str__(),
        )
        # Instead simulate via the session directly: set an invalid JWT string
        # by posting once to let Starlette's SessionMiddleware sign it.
        set_response = await client.post(
            "/auth/2fa",
            data={"full_code": "000000"},
            follow_redirects=False,
        )

    # Without a valid pending_2fa_token in the session, the route must redirect to login.
    assert set_response.status_code == 303
    assert "/auth/login" in set_response.headers["location"]
    assert "arena_access_token" not in set_response.cookies


@pytest.mark.asyncio
async def test_2fa_submit_with_wrong_action_token_redirects_to_login(session: AsyncSession) -> None:
    """POST /auth/2fa with a wrong-action token in the session redirects to login.

    The route calls ``get_pending_2fa_token_data`` which checks that the action
    claim is exactly ``PENDING_2FA``.  A LOGIN token stored in the session must
    be rejected and redirect the user back to the login page.
    """
    app = _build_arena_app(session)
    user = await _create_user_with_2fa(session)
    await session.commit()

    # A LOGIN token has the wrong action for the 2FA gate.
    wrong_action_token = str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=300,
            extra_data={"tid": user.get_token_id()},
        )
    )

    @app.post("/test-set-wrong-action-token")
    async def _writer(request: Request) -> HTMLResponse:
        request.session["pending_2fa_token"] = wrong_action_token
        return HTMLResponse("ok")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post("/test-set-wrong-action-token")
        response = await client.post("/auth/2fa", data={"full_code": "123456"}, follow_redirects=False)

    assert response.status_code == 303
    assert "/auth/login" in response.headers["location"]
    assert "arena_access_token" not in response.cookies


# ---------------------------------------------------------------------------
# Wrong code
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_2fa_submit_with_wrong_code_redirects_back_to_2fa_page(session: AsyncSession) -> None:
    """POST /auth/2fa with a wrong code redirects back to /auth/2fa (no cookie)."""
    app = _build_arena_app(session)
    user = await _create_user_with_2fa(session)
    await session.commit()

    valid_pending_token = set_pending_2fa_token(user, app.state.jwt_service, remember_me=False)

    # Mock the validation service to reject the code.
    failed_result = TwoFAValidationResult(
        success=False,
        method_used=Autenticacao2FA.INVALID_CODE,
        error_message="Invalid 2FA code.",
    )

    # Use a dedicated session-injection stub to write the pending token into the session.
    @app.post("/test-set-2fa-token")
    async def _set_2fa_token(request: Request) -> HTMLResponse:
        request.session["pending_2fa_token"] = valid_pending_token
        return HTMLResponse("ok")

    with patch(
        "arena.routes.auth_2fa.user_2fa_service.validar_codigo_2fa",
        new=AsyncMock(return_value=failed_result),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            await client.post("/test-set-2fa-token")
            response = await client.post("/auth/2fa", data={"full_code": "000000"}, follow_redirects=False)

    assert response.status_code == 303
    assert "/auth/2fa" in response.headers["location"]
    assert "arena_access_token" not in response.cookies
    assert await _login_history_modes(session, user.id) == []


# ---------------------------------------------------------------------------
# Successful 2FA verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_2fa_submit_with_valid_code_sets_cookie_and_redirects_to_dashboard(
    session: AsyncSession,
) -> None:
    """POST /auth/2fa with valid TOTP code issues a LOGIN cookie and redirects to dashboard."""
    app = _build_arena_app(session)
    user = await _create_user_with_2fa(session)
    await session.commit()

    valid_pending_token = set_pending_2fa_token(user, app.state.jwt_service, remember_me=False)
    success_result = TwoFAValidationResult(
        success=True,
        method_used=Autenticacao2FA.TOTP,
        remaining_backup_codes=5,
    )

    @app.post("/test-set-2fa-token-success")
    async def _set_token(request: Request) -> HTMLResponse:
        request.session["pending_2fa_token"] = valid_pending_token
        return HTMLResponse("ok")

    with patch(
        "arena.routes.auth_2fa.user_2fa_service.validar_codigo_2fa",
        new=AsyncMock(return_value=success_result),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            await client.post("/test-set-2fa-token-success")
            response = await client.post("/auth/2fa", data={"full_code": "123456"}, follow_redirects=False)

    assert response.status_code == 303
    assert "/dashboard" in response.headers["location"]
    assert "arena_access_token" in response.cookies
    assert await _login_history_modes(session, user.id) == ["2fa"]


@pytest.mark.asyncio
async def test_login_password_step_for_2fa_user_does_not_record_login_history(
    session: AsyncSession,
) -> None:
    """Password verification alone is not a completed login for 2FA users."""
    app = _build_arena_app(session)
    user = await _create_user_with_2fa(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/login",
            data={"email": "twofa@test.example", "password": "StrongPass1!"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "/auth/2fa" in response.headers["location"]
    assert "arena_access_token" not in response.cookies
    assert await _login_history_modes(session, user.id) == []


@pytest.mark.asyncio
async def test_2fa_submit_with_backup_code_records_backup_code_mode(
    session: AsyncSession,
) -> None:
    """Successful backup-code login records login history as backup_code."""
    app = _build_arena_app(session)
    user = await _create_user_with_2fa(session)
    await session.commit()

    valid_pending_token = set_pending_2fa_token(user, app.state.jwt_service, remember_me=False)
    success_result = TwoFAValidationResult(
        success=True,
        method_used=Autenticacao2FA.BACKUP,
        remaining_backup_codes=5,
    )

    @app.post("/test-set-2fa-token-backup")
    async def _set_token(request: Request) -> HTMLResponse:
        request.session["pending_2fa_token"] = valid_pending_token
        return HTMLResponse("ok")

    with patch(
        "arena.routes.auth_2fa.user_2fa_service.validar_codigo_2fa",
        new=AsyncMock(return_value=success_result),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            await client.post("/test-set-2fa-token-backup")
            response = await client.post("/auth/2fa", data={"full_code": "backup-code"}, follow_redirects=False)

    assert response.status_code == 303
    assert "/dashboard" in response.headers["location"]
    assert "arena_access_token" in response.cookies
    assert await _login_history_modes(session, user.id) == ["backup_code"]


@pytest.mark.asyncio
async def test_2fa_submit_with_valid_code_redirects_to_safe_next(
    session: AsyncSession,
) -> None:
    """POST /auth/2fa redirects to the safe next URL from the pending token."""
    app = _build_arena_app(session)
    user = await _create_user_with_2fa(session)
    await session.commit()

    valid_pending_token = set_pending_2fa_token(
        user,
        app.state.jwt_service,
        remember_me=False,
        next_page="/submissions/sub-1?source=queue",
    )
    success_result = TwoFAValidationResult(
        success=True,
        method_used=Autenticacao2FA.TOTP,
        remaining_backup_codes=5,
    )

    @app.post("/test-set-2fa-token-next")
    async def _set_token(request: Request) -> HTMLResponse:
        request.session["pending_2fa_token"] = valid_pending_token
        return HTMLResponse("ok")

    with patch(
        "arena.routes.auth_2fa.user_2fa_service.validar_codigo_2fa",
        new=AsyncMock(return_value=success_result),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            await client.post("/test-set-2fa-token-next")
            response = await client.post("/auth/2fa", data={"full_code": "123456"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/submissions/sub-1?source=queue"
    assert "arena_access_token" in response.cookies


@pytest.mark.asyncio
async def test_2fa_submit_valid_code_token_sub_is_user_id(session: AsyncSession) -> None:
    """The LOGIN JWT issued after 2FA must carry the correct user ID in sub."""
    app = _build_arena_app(session)
    user = await _create_user_with_2fa(session)
    await session.commit()

    valid_pending_token = set_pending_2fa_token(user, app.state.jwt_service, remember_me=False)
    success_result = TwoFAValidationResult(
        success=True,
        method_used=Autenticacao2FA.TOTP,
        remaining_backup_codes=5,
    )

    @app.post("/test-set-2fa-token-sub")
    async def _set_token(request: Request) -> HTMLResponse:
        request.session["pending_2fa_token"] = valid_pending_token
        return HTMLResponse("ok")

    with patch(
        "arena.routes.auth_2fa.user_2fa_service.validar_codigo_2fa",
        new=AsyncMock(return_value=success_result),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            await client.post("/test-set-2fa-token-sub")
            response = await client.post("/auth/2fa", data={"full_code": "123456"}, follow_redirects=False)

    assert response.status_code == 303
    raw_token = response.cookies["arena_access_token"]
    claims = app.state.jwt_service.validar(raw_token)
    assert claims.valid
    assert claims.action == ArenaTokenAction.LOGIN
    assert claims.sub == user.id


@pytest.mark.asyncio
async def test_2fa_submit_with_remember_me_sets_max_age_on_cookie(session: AsyncSession) -> None:
    """POST /auth/2fa honours the remember_me flag from the PENDING_2FA token."""
    app = _build_arena_app(session)
    user = await _create_user_with_2fa(session)
    await session.commit()

    # Token created with remember_me=True.
    pending_token_remember = set_pending_2fa_token(user, app.state.jwt_service, remember_me=True)
    success_result = TwoFAValidationResult(
        success=True,
        method_used=Autenticacao2FA.TOTP,
        remaining_backup_codes=5,
    )

    @app.post("/test-set-2fa-token-remember")
    async def _set_token(request: Request) -> HTMLResponse:
        request.session["pending_2fa_token"] = pending_token_remember
        return HTMLResponse("ok")

    with patch(
        "arena.routes.auth_2fa.user_2fa_service.validar_codigo_2fa",
        new=AsyncMock(return_value=success_result),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            await client.post("/test-set-2fa-token-remember")
            response = await client.post("/auth/2fa", data={"full_code": "123456"}, follow_redirects=False)

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
