#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the Arena user security endpoints.

Covers:
- GET /user/profile/2fa/setup  — unauthenticated → login redirect
- GET /user/profile/2fa/setup  — authenticated, 2FA not enabled → 200 with QR page
- GET /user/profile/2fa/setup  — authenticated, 2FA already enabled → profile redirect
- POST /user/profile/2fa/disable — wrong password → profile redirect
- POST /user/profile/2fa/disable — correct password, user has 2FA → 2FA disabled
- POST /user/profile/backup-codes/regenerate — user without 2FA → profile redirect
- POST /user/profile/backup-codes/regenerate — user with 2FA → backup-codes redirect
- GET /user/profile/backup-codes  — codes in session → 200
- GET /user/profile/backup-codes  — no codes in session → profile redirect
- POST /user/profile/2fa/confirm — invalid/expired setup token → setup redirect

These tests mock ``user_2fa_service.iniciar_ativacao_2fa`` for setup tests so
that they do not require a SecretsManager initialisation (the EncryptedString
TypeDecorator only fires when the ORM writes the ``_otp_secret`` column).
"""

import logging
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_users  # noqa: F401
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_auth_records import ArenaBackup2FA
from arena.models.arena_users import ArenaUser
from arena.routes.auth import router as arena_auth_router
from arena.routes.help import router as arena_help_router
from arena.routes.ranking import router as arena_ranking_router
from arena.routes.user_security import router as arena_user_security_router
from arena.services import backup2fa_service
from arena.services.qrcode_service import QRCodeService
from arena.services.token_service import ArenaTokenAction
from arena.services.user_2fa_service import Autenticacao2FA, TwoFASetupResult
from shared.enumerations import ArenaRole

_TEST_JWT_SECRET = "test-secret-key-for-arena-security-tests!32bytes"


def _build_arena_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena app for user-security route tests.

    Registers ``ArenaAuthMiddleware``, ``SessionMiddleware``, the
    ``arena_auth_router`` (for the ``arena_login`` named route), and the
    ``arena_user_security_router``.  Also mounts static assets and sets up
    stub routes for all named endpoints referenced in redirects.

    Args:
        session: Async database session used to back the session factory.

    Returns:
        FastAPI: Minimal application suitable for security-route testing.
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
    app.state.qrcode_service = QRCodeService.create_default()
    app.state.email_service = MagicMock(send_email=MagicMock(return_value=MagicMock(success=True)))

    shared_dir = arena_dir.parent / "shared"
    app.mount("/static/css", StaticFiles(directory=arena_dir / "static" / "css"), name="arena_static_css")
    app.mount("/static/js", StaticFiles(directory=arena_dir / "static" / "js"), name="arena_static_js")
    app.mount("/static/img", StaticFiles(directory=arena_dir / "static" / "img"), name="arena_static_img")
    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/shared-js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")

    # ---- Named stubs required for redirects / templates ----

    @app.get("/auth/login", name="arena_login")
    async def _stub_login() -> Response:
        """Stub login page."""
        return Response("login")

    @app.post("/auth/login", name="arena_login_submit")
    async def _stub_login_post() -> Response:
        """Stub login POST."""
        return Response("ok")

    @app.post("/auth/logout", name="arena_logout")
    async def _stub_logout() -> Response:
        """Stub logout."""
        return Response("ok")

    @app.get("/auth/signup", name="arena_signup")
    async def _stub_signup() -> Response:
        """Stub signup page."""
        return Response("signup")

    @app.get("/user/profile", name="arena_user_profile")
    async def _stub_profile() -> HTMLResponse:
        """Stub user profile page."""
        return HTMLResponse("profile")

    @app.get("/dashboard", name="arena_dashboard")
    async def _stub_dashboard() -> HTMLResponse:
        """Stub dashboard page."""
        return HTMLResponse("dashboard")

    @app.get("/status", name="arena_status")
    async def _stub_status() -> HTMLResponse:
        """Stub status page."""
        return HTMLResponse("status")

    @app.get("/user/profile/backup-codes-display", name="arena_backup_codes")
    async def _stub_backup_codes_redirect() -> HTMLResponse:
        """Stub backup codes display endpoint."""
        return HTMLResponse("backup codes")

    @app.get("/image/users/{user_id}/avatar", name="arena_user_avatar_by_id")
    async def _stub_avatar(user_id: str) -> HTMLResponse:
        """Stub avatar endpoint."""
        return HTMLResponse("avatar")

    @app.get("/problems", name="arena_problem_list")
    async def _stub_problem_list() -> Response:
        """Stub problem list."""
        return Response("problems")

    @app.get("/classes", name="arena_classes_index")
    async def _stub_classes_index() -> Response:
        """Stub class list."""
        return Response("classes")

    @app.get("/classes/registered", name="arena_classes_registered")
    async def _stub_classes_registered() -> Response:
        return Response("classes registered")

    @app.get("/classes/open", name="arena_classes_open")
    async def _stub_classes_open() -> Response:
        return Response("classes open")

    @app.get("/classes/manage", name="arena_classes_manage")
    async def _stub_classes_manage() -> Response:
        return Response("classes manage")

    @app.get("/admin/problems", name="arena_admin_problem_list")
    async def _stub_admin_problems() -> Response:
        """Stub admin problems."""
        return Response("problems")

    @app.get("/admin/dashboard", name="arena_admin_dashboard")
    async def _admin_dashboard_stub() -> Response:
        return Response("dashboard")

    @app.get("/admin/categories", name="arena_admin_category_list")
    async def _stub_admin_categories() -> Response:
        """Stub admin categories."""
        return Response("categories")

    @app.get("/admin/affiliations", name="arena_admin_affiliation_list")
    async def _stub_admin_affiliations() -> Response:
        """Stub admin affiliations."""
        return Response("affiliations")

    @app.get("/arena/notifications", name="arena_notifications_list")
    async def _stub_notifications() -> Response:
        """Stub notifications list."""
        return Response("[]", media_type="application/json")

    # Register auth router (for arena_login + arena_2fa named routes needed by templates)
    app.include_router(arena_auth_router)
    app.include_router(arena_help_router)
    app.include_router(arena_user_security_router)
    app.include_router(arena_ranking_router)
    return app


async def _create_active_user(
    session: AsyncSession,
    email: str = "security@test.example",
    usa_2fa: bool = False,
) -> ArenaUser:
    """Create and persist an active Arena user for security-route tests.

    Args:
        session: Async database session.
        email: Normalised email to assign.
        usa_2fa: Whether 2FA is enabled for this user.

    Returns:
        ArenaUser: Persisted user with the given configuration.
    """
    user = ArenaUser(
        id=str(uuid.uuid4()),
        nome="Security Test User",
        email_normalizado=email,
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        com_foto=False,
        usa_2fa=usa_2fa,
        precisa_trocar_senha=False,
        session_version=1,
    )
    user.password = "StrongPass1!"
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _login_token(app: FastAPI, user: ArenaUser) -> str:
    """Issue a valid Arena LOGIN JWT for the supplied user.

    Args:
        app: FastAPI application whose jwt_service is used for signing.
        user: Arena user to issue the token for.

    Returns:
        str: Signed JWT token string.
    """
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )


# ---------------------------------------------------------------------------
# GET /user/profile/2fa/setup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_2fa_setup_unauthenticated_redirects_to_login(session: AsyncSession) -> None:
    """GET /user/profile/2fa/setup without auth redirects to login."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/user/profile/2fa/setup", follow_redirects=False)

    assert response.status_code == 303
    assert "/auth/login" in response.headers["location"]


@pytest.mark.asyncio
async def test_2fa_setup_authenticated_no_2fa_renders_setup_page(session: AsyncSession) -> None:
    """GET /user/profile/2fa/setup for a user without 2FA renders the setup page (200)."""
    user = await _create_active_user(session, usa_2fa=False)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    # Mock iniciar_ativacao_2fa so we do not trigger EncryptedString DB write.
    setup_result = TwoFASetupResult(
        status=Autenticacao2FA.ENABLING,
        token=str(
            app.state.jwt_service.criar(
                action=ArenaTokenAction.ACTIVATING_2FA,
                sub=user.id,
                expires_in=90,
            )
        ),
    )

    # Also mock the qrcode service on app.state and otp_secret_formatado to avoid
    # hitting the EncryptedString column (which requires SecretsManager in tests).
    fake_qr_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    app.state.qrcode_service = MagicMock(
        generate_totp_qrcode=MagicMock(return_value=fake_qr_b64),
    )

    with (
        patch(
            "arena.routes.user_security.user_2fa_service.iniciar_ativacao_2fa",
            new=AsyncMock(return_value=setup_result),
        ),
        patch(
            "arena.routes.user_security.user_2fa_service.otp_secret_formatado",
            return_value="ABCD EFGH IJKL MNOP",
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            client.cookies.set("arena_access_token", token)
            response = await client.get("/user/profile/2fa/setup", follow_redirects=False)

    assert response.status_code == 200
    assert "2fa" in response.text.lower() or "authenticator" in response.text.lower() or "qr" in response.text.lower()


@pytest.mark.asyncio
async def test_2fa_setup_authenticated_2fa_already_enabled_redirects_to_profile(
    session: AsyncSession,
) -> None:
    """GET /user/profile/2fa/setup for a user with 2FA already enabled redirects to profile."""
    user = await _create_active_user(session, usa_2fa=True)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    already_enabled_result = TwoFASetupResult(status=Autenticacao2FA.ALREADY_ENABLED)

    with patch(
        "arena.routes.user_security.user_2fa_service.iniciar_ativacao_2fa",
        new=AsyncMock(return_value=already_enabled_result),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            client.cookies.set("arena_access_token", token)
            response = await client.get("/user/profile/2fa/setup", follow_redirects=False)

    assert response.status_code == 303
    assert "/user/profile" in response.headers["location"]


# ---------------------------------------------------------------------------
# POST /user/profile/2fa/confirm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_2fa_confirm_unauthenticated_redirects_to_login(session: AsyncSession) -> None:
    """POST /user/profile/2fa/confirm without auth redirects to login."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/user/profile/2fa/confirm",
            data={"full_code": "123456"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "/auth/login" in response.headers["location"]


@pytest.mark.asyncio
async def test_2fa_confirm_with_invalid_session_token_redirects_to_setup(
    session: AsyncSession,
) -> None:
    """POST /user/profile/2fa/confirm with no/invalid setup token redirects to setup."""
    user = await _create_active_user(session, usa_2fa=False)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    # Mock validar_token_ativacao_2fa to return MISSING_TOKEN (no token in session).
    invalid_result = TwoFASetupResult(status=Autenticacao2FA.MISSING_TOKEN)

    with patch(
        "arena.routes.user_security.user_2fa_service.validar_token_ativacao_2fa",
        return_value=invalid_result,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            client.cookies.set("arena_access_token", token)
            response = await client.post(
                "/user/profile/2fa/confirm",
                data={"full_code": "123456"},
                follow_redirects=False,
            )

    assert response.status_code == 303
    assert "/user/profile/2fa/setup" in response.headers["location"]


@pytest.mark.asyncio
async def test_2fa_confirm_with_wrong_code_redirects_to_setup(session: AsyncSession) -> None:
    """POST /user/profile/2fa/confirm with an invalid TOTP code redirects back to setup."""
    user = await _create_active_user(session, usa_2fa=False)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    enabling_result = TwoFASetupResult(
        status=Autenticacao2FA.ENABLING,
        secret="JBSWY3DPEHPK3PXP",
        user_id=user.id,
    )
    invalid_code_result = TwoFASetupResult(status=Autenticacao2FA.INVALID_CODE)

    with (
        patch(
            "arena.routes.user_security.user_2fa_service.validar_token_ativacao_2fa",
            return_value=enabling_result,
        ),
        patch(
            "arena.routes.user_security.user_2fa_service.confirmar_ativacao_2fa",
            new=AsyncMock(return_value=invalid_code_result),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            client.cookies.set("arena_access_token", token)
            response = await client.post(
                "/user/profile/2fa/confirm",
                data={"full_code": "000000"},
                follow_redirects=False,
            )

    assert response.status_code == 303
    assert "/user/profile/2fa/setup" in response.headers["location"]


# ---------------------------------------------------------------------------
# POST /user/profile/2fa/disable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_2fa_disable_unauthenticated_redirects_to_login(session: AsyncSession) -> None:
    """POST /user/profile/2fa/disable without auth redirects to login."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/user/profile/2fa/disable",
            data={"password": "StrongPass1!"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "/auth/login" in response.headers["location"]


@pytest.mark.asyncio
async def test_2fa_disable_with_wrong_password_redirects_to_profile(
    session: AsyncSession,
) -> None:
    """POST /user/profile/2fa/disable with incorrect password redirects to profile."""
    user = await _create_active_user(session, usa_2fa=True)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.post(
            "/user/profile/2fa/disable",
            data={"password": "WrongPassword1!"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "/user/profile" in response.headers["location"]


@pytest.mark.asyncio
async def test_2fa_disable_with_correct_password_disables_2fa(session: AsyncSession) -> None:
    """POST /user/profile/2fa/disable with correct password disables 2FA and redirects."""
    user = await _create_active_user(session, usa_2fa=True)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    disabled_result = TwoFASetupResult(status=Autenticacao2FA.DISABLED)

    with patch(
        "arena.routes.user_security.user_2fa_service.desativar_2fa",
        new=AsyncMock(return_value=disabled_result),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            client.cookies.set("arena_access_token", token)
            response = await client.post(
                "/user/profile/2fa/disable",
                data={"password": "StrongPass1!"},
                follow_redirects=False,
            )

    assert response.status_code == 303
    assert "/user/profile" in response.headers["location"]


@pytest.mark.asyncio
async def test_2fa_disable_without_2fa_enabled_redirects_to_profile(
    session: AsyncSession,
) -> None:
    """POST /user/profile/2fa/disable for a user without 2FA redirects with warning."""
    user = await _create_active_user(session, usa_2fa=False)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.post(
            "/user/profile/2fa/disable",
            data={"password": "StrongPass1!"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "/user/profile" in response.headers["location"]


# ---------------------------------------------------------------------------
# POST /user/profile/backup-codes/regenerate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backup_codes_regenerate_unauthenticated_redirects_to_login(
    session: AsyncSession,
) -> None:
    """POST /user/profile/backup-codes/regenerate without auth redirects to login."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/user/profile/backup-codes/regenerate",
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "/auth/login" in response.headers["location"]


@pytest.mark.asyncio
async def test_backup_codes_regenerate_without_2fa_redirects_to_profile(
    session: AsyncSession,
) -> None:
    """POST backup-codes/regenerate for a user without 2FA redirects to profile."""
    user = await _create_active_user(session, usa_2fa=False)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.post(
            "/user/profile/backup-codes/regenerate",
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "/user/profile" in response.headers["location"]


@pytest.mark.asyncio
async def test_backup_codes_regenerate_with_2fa_redirects_to_backup_codes_page(
    session: AsyncSession,
) -> None:
    """POST backup-codes/regenerate for a user with 2FA redirects to backup codes page."""
    user = await _create_active_user(session, usa_2fa=True)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    fake_codes = [f"CODE{index:02d}" for index in range(10)]
    gerar_novos_codigos = AsyncMock(return_value=fake_codes)

    with patch(
        "arena.routes.user_security.backup2fa_service.gerar_novos_codigos",
        new=gerar_novos_codigos,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            client.cookies.set("arena_access_token", token)
            response = await client.post(
                "/user/profile/backup-codes/regenerate",
                follow_redirects=False,
            )

    assert response.status_code == 303
    # Route redirects to arena_backup_codes
    location = response.headers["location"]
    assert "backup-codes" in location or "backup_codes" in location or "backup" in location.lower()
    gerar_novos_codigos.assert_awaited_once()
    assert gerar_novos_codigos.await_args.kwargs == {"quantidade": 10}


# ---------------------------------------------------------------------------
# GET /user/profile/backup-codes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backup_codes_page_unauthenticated_redirects_to_login(
    session: AsyncSession,
) -> None:
    """GET /user/profile/backup-codes without auth redirects to login."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/user/profile/backup-codes", follow_redirects=False)

    assert response.status_code == 303
    assert "/auth/login" in response.headers["location"]


@pytest.mark.asyncio
async def test_backup_codes_page_without_codes_in_session_redirects_to_profile(
    session: AsyncSession,
) -> None:
    """GET /user/profile/backup-codes with no codes in session redirects to profile."""
    user = await _create_active_user(session)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/user/profile/backup-codes", follow_redirects=False)

    assert response.status_code == 303
    assert "/user/profile" in response.headers["location"]


@pytest.mark.asyncio
async def test_backup_codes_page_with_codes_in_session_renders_page(
    session: AsyncSession,
) -> None:
    """GET /user/profile/backup-codes with codes in session renders the codes page (200)."""
    user = await _create_active_user(session)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    fake_codes = ["AAABBB", "CCCDDD", "EEEFFF"]

    # Inject the codes into the session via a POST stub.
    @app.post("/test-set-backup-codes")
    async def _writer(request: Request) -> HTMLResponse:
        request.session["new_backup_codes"] = fake_codes
        return HTMLResponse("ok")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        await client.post("/test-set-backup-codes")
        response = await client.get("/user/profile/backup-codes", follow_redirects=False)

    assert response.status_code == 200
    # At least one backup code must appear in the rendered page.
    assert any(code in response.text for code in fake_codes)


@pytest.mark.asyncio
async def test_backup_codes_page_codes_consumed_from_session_on_first_visit(
    session: AsyncSession,
) -> None:
    """GET /user/profile/backup-codes removes codes from session after first view."""
    user = await _create_active_user(session)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    fake_codes = ["AAABBB", "CCCDDD"]
    captured_session: dict = {}

    @app.post("/test-set-codes-consume")
    async def _writer(request: Request) -> HTMLResponse:
        request.session["new_backup_codes"] = fake_codes
        return HTMLResponse("ok")

    @app.get("/test-read-session-after")
    async def _reader(request: Request) -> HTMLResponse:
        captured_session.update(dict(request.session))
        return HTMLResponse("ok")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        await client.post("/test-set-codes-consume")
        # First visit: codes must be rendered and then popped from the session.
        first_response = await client.get("/user/profile/backup-codes", follow_redirects=False)
        await client.get("/test-read-session-after")

    assert first_response.status_code == 200
    # Codes must be gone from the session after the first visit.
    assert "new_backup_codes" not in captured_session


@pytest.mark.asyncio
async def test_backup_codes_regenerate_stores_codes_in_session(
    session: AsyncSession,
) -> None:
    """POST backup-codes/regenerate stores generated codes in the session for one display."""
    user = await _create_active_user(session, usa_2fa=True)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    fake_codes = ["XYZABC", "DEFGHI"]
    captured_session: dict = {}

    @app.get("/test-read-regen-session")
    async def _reader(request: Request) -> HTMLResponse:
        captured_session.update(dict(request.session))
        return HTMLResponse("ok")

    with patch(
        "arena.routes.user_security.backup2fa_service.gerar_novos_codigos",
        new=AsyncMock(return_value=fake_codes),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            client.cookies.set("arena_access_token", token)
            await client.post("/user/profile/backup-codes/regenerate", follow_redirects=False)
            await client.get("/test-read-regen-session")

    assert captured_session.get("new_backup_codes") == fake_codes


@pytest.mark.asyncio
async def test_backup_codes_regenerate_invalidates_old_codes_and_stores_ten_new_codes(
    session: AsyncSession,
) -> None:
    """Regenerating backup codes should invalidate old codes and persist 10 new ones."""
    user = await _create_active_user(session, usa_2fa=True)
    old_codes = await backup2fa_service.gerar_novos_codigos(user, session, quantidade=3)
    await session.commit()
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.post("/user/profile/backup-codes/regenerate", follow_redirects=False)
        display_response = await client.get("/user/profile/backup-codes", follow_redirects=False)

    assert response.status_code == 303
    assert display_response.status_code == 200
    assert display_response.text.count("arena-backup-code-cell") == 10
    assert not await backup2fa_service.consumir_token(user, old_codes[0], session)

    codes = (
        await session.execute(
            select(ArenaBackup2FA).where(
                ArenaBackup2FA.arena_user_id == user.id,
                ArenaBackup2FA.utilizado.is_(False),
            )
        )
    ).scalars()
    unused_codes = list(codes)
    assert len(unused_codes) == 10
