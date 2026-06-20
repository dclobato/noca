#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the Arena login and logout flows.

Covers POST /auth/login (success, remember-me, wrong password, nonexistent user,
inactive/unconfirmed, inactive/confirmed) and POST /auth/logout (no cookie,
valid cookie).
"""

import logging
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_users  # noqa: F401
from arena.models.arena_auth_records import ArenaLoginHistory
from arena.models.arena_users import ArenaUser
from arena.routes.auth import router as arena_auth_router
from arena.routes.auth_2fa import router as arena_auth_2fa_router
from arena.routes.auth_password import router as arena_auth_password_router
from arena.routes.auth_signup import router as arena_auth_signup_router
from arena.services.token_service import ArenaTokenAction
from shared.enumerations import ArenaRole
from shared.services.email_service import EmailConfig, EmailService
from shared.services.imageprocessing_service import ImageProcessingConfig, ImageProcessingService

_TEST_JWT_SECRET = "test-secret-key-for-arena-tests-only-32bytes"
_DEFAULT_LOGIN_SECONDS = 3600
_REMEMBER_ME_SECONDS = 30 * 24 * 3600


def _build_arena_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena FastAPI app for login/logout route tests.

    Includes a stub ``arena_dashboard`` route so that a successful login can
    resolve the redirect URL without a full arena setup.
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

    # Stub routes required for arena_login_submit URL resolution.
    stubs = APIRouter()

    @stubs.get("/dashboard", name="arena_dashboard")
    async def stub_dashboard(request: Request) -> HTMLResponse:
        """Stub dashboard endpoint."""
        return HTMLResponse("dashboard")

    @stubs.get("/user/profile/complete", name="arena_user_profile_completion")
    async def stub_profile_completion(request: Request) -> HTMLResponse:
        """Stub profile completion endpoint."""
        return HTMLResponse("profile completion")

    @stubs.get("/status", name="arena_status")
    async def stub_status(request: Request) -> HTMLResponse:
        """Stub status endpoint."""
        return HTMLResponse("status")

    @stubs.get("/classes", name="arena_classes_index")
    async def stub_classes_index(request: Request) -> HTMLResponse:
        """Stub class list endpoint."""
        return HTMLResponse("classes")

    @stubs.get("/classes/registered", name="arena_classes_registered")
    async def stub_classes_registered(request: Request) -> HTMLResponse:
        return HTMLResponse("classes registered")

    @stubs.get("/classes/open", name="arena_classes_open")
    async def stub_classes_open(request: Request) -> HTMLResponse:
        return HTMLResponse("classes open")

    @stubs.get("/classes/manage", name="arena_classes_manage")
    async def stub_classes_manage(request: Request) -> HTMLResponse:
        return HTMLResponse("classes manage")

    @stubs.get("/image/users/{user_id}/avatar", name="arena_user_avatar_by_id")
    async def stub_avatar(user_id: str) -> HTMLResponse:
        """Stub avatar endpoint."""
        return HTMLResponse("avatar")

    app.include_router(stubs)
    shared_dir = arena_dir.parent / "shared"
    app.mount("/static/css", StaticFiles(directory=arena_dir / "static" / "css"), name="arena_static_css")
    app.mount("/static/js", StaticFiles(directory=arena_dir / "static" / "js"), name="arena_static_js")
    app.mount("/static/img", StaticFiles(directory=arena_dir / "static" / "img"), name="arena_static_img")
    app.mount("/static/shared-js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")
    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.include_router(arena_auth_router)
    app.include_router(arena_auth_signup_router)
    app.include_router(arena_auth_2fa_router)
    app.include_router(arena_auth_password_router)
    return app


async def _create_active_user(
    session: AsyncSession,
    email: str = "user@test.example",
    password: str = "StrongPass1!",
    *,
    complete_profile: bool = True,
) -> ArenaUser:
    """Create and persist an active, email-confirmed ArenaUser for login tests.

    Args:
        session: Async database session.
        email: Normalised email to assign.
        password: Plaintext password (stored as hash).

    Returns:
        ArenaUser: The newly created, flushed user.
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
        aceitou_termos_privacidade=True,
        com_foto=False,
        usa_2fa=False,
        precisa_trocar_senha=False,
        session_version=1,
        affiliation_id="test-affiliation" if complete_profile else None,
        preferred_language_id="python" if complete_profile else None,
        country_code="BR" if complete_profile else None,
        prefered_language="en-US",
    )
    user.password = password
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_login_incomplete_profile_overrides_safe_next(session: AsyncSession) -> None:
    """Completed password login prioritizes the profile completion notice."""
    app = _build_arena_app(session)
    await _create_active_user(session, complete_profile=False)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/login",
            data={
                "email": "user@test.example",
                "password": "StrongPass1!",
                "next": "/submissions/sub-1",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"].endswith("/user/profile/complete")


async def _create_inactive_user(
    session: AsyncSession,
    email: str,
    email_confirmado: bool = False,
    password: str = "StrongPass1!",
) -> ArenaUser:
    """Create and persist an inactive ArenaUser.

    Args:
        session: Async database session.
        email: Normalised email to assign.
        email_confirmado: Whether the email has been confirmed.
        password: Plaintext password (stored as hash).

    Returns:
        ArenaUser: The newly created, flushed user.
    """
    user = ArenaUser(
        id=str(uuid.uuid4()),
        nome="Inactive User",
        email_normalizado=email,
        role=ArenaRole.ARENA_USER,
        ativo=False,
        email_confirmado=email_confirmado,
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


async def _login_history_modes(session: AsyncSession, user_id: str) -> list[str | None]:
    """Return recorded login-history modes for a user in insertion order."""
    result = await session.execute(
        select(ArenaLoginHistory.mode).where(ArenaLoginHistory.arena_user_id == user_id).order_by(ArenaLoginHistory.id)
    )
    return list(result.scalars())


# ---------------------------------------------------------------------------
# Login page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_page_renders(session: AsyncSession) -> None:
    """GET /auth/login returns 200 with the login form."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/login")

    assert response.status_code == 200
    assert "Log In" in response.text


@pytest.mark.asyncio
async def test_login_page_renders_next_hidden_field(session: AsyncSession) -> None:
    """GET /auth/login preserves a safe next candidate as a hidden form field."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/login?next=/problems/42%3Ftab%3Dsubmit")

    assert response.status_code == 200
    assert 'name="next"' in response.text
    assert 'value="/problems/42?tab=submit"' in response.text


# ---------------------------------------------------------------------------
# Successful login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_success_sets_cookie_and_redirects_to_dashboard(session: AsyncSession) -> None:
    """Valid credentials issue a session cookie and redirect to the dashboard."""
    app = _build_arena_app(session)
    await _create_active_user(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/login",
            data={"email": "user@test.example", "password": "StrongPass1!"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "/dashboard" in response.headers["location"]
    assert "arena_access_token" in response.cookies


@pytest.mark.asyncio
async def test_login_success_redirects_to_safe_next(session: AsyncSession) -> None:
    """Successful direct login redirects to a same-origin next URL."""
    app = _build_arena_app(session)
    await _create_active_user(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/login",
            data={
                "email": "user@test.example",
                "password": "StrongPass1!",
                "next": "/submissions/sub-1?source=queue",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/submissions/sub-1?source=queue"
    assert "arena_access_token" in response.cookies


@pytest.mark.asyncio
async def test_login_success_unsafe_next_falls_back_to_dashboard(session: AsyncSession) -> None:
    """Unsafe login next values cannot redirect away from the Arena origin."""
    app = _build_arena_app(session)
    await _create_active_user(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/login",
            data={
                "email": "user@test.example",
                "password": "StrongPass1!",
                "next": "//evil.example/path",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "/dashboard" in response.headers["location"]
    assert "arena_access_token" in response.cookies


@pytest.mark.asyncio
async def test_login_success_with_remember_me_sets_max_age(session: AsyncSession) -> None:
    """Checking 'remember me' on login makes the cookie persistent (max_age set)."""
    app = _build_arena_app(session)
    await _create_active_user(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/login",
            data={"email": "user@test.example", "password": "StrongPass1!", "remember": "on"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "arena_access_token" in response.cookies
    cookie_header = response.headers.get("set-cookie", "")
    assert "max-age=" in cookie_header.lower()


@pytest.mark.asyncio
async def test_login_without_remember_me_keeps_default_token_lifetime(session: AsyncSession) -> None:
    """Regular Arena logins keep the default 1-hour LOGIN token lifetime."""
    app = _build_arena_app(session)
    await _create_active_user(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/login",
            data={"email": "user@test.example", "password": "StrongPass1!"},
            follow_redirects=False,
        )

    claims = app.state.jwt_service.validar(response.cookies["arena_access_token"])
    assert claims.valid
    assert claims.expires_in is not None
    assert _DEFAULT_LOGIN_SECONDS - 5 <= claims.expires_in <= _DEFAULT_LOGIN_SECONDS
    assert claims.extra_data is not None
    assert claims.extra_data.get("remember_me") is False


@pytest.mark.asyncio
async def test_login_with_remember_me_uses_default_token_lifetime_and_tracks_session_start(
    session: AsyncSession,
) -> None:
    """Remembered Arena logins keep 1-hour tokens and carry the original session start."""
    app = _build_arena_app(session)
    await _create_active_user(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/login",
            data={"email": "user@test.example", "password": "StrongPass1!", "remember": "on"},
            follow_redirects=False,
        )

    claims = app.state.jwt_service.validar(response.cookies["arena_access_token"])
    assert claims.valid
    assert claims.expires_in is not None
    assert _DEFAULT_LOGIN_SECONDS - 5 <= claims.expires_in <= _DEFAULT_LOGIN_SECONDS
    assert claims.extra_data is not None
    assert claims.extra_data.get("remember_me") is True
    assert isinstance(claims.extra_data.get("session_started_at"), int)


@pytest.mark.asyncio
async def test_login_token_carries_tid_extra_data(session: AsyncSession) -> None:
    """The issued LOGIN JWT must carry extra_data.tid = user.get_token_id()."""
    app = _build_arena_app(session)
    user = await _create_active_user(session)
    await session.commit()
    expected_tid = user.get_token_id()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/login",
            data={"email": "user@test.example", "password": "StrongPass1!"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    raw_token = response.cookies["arena_access_token"]
    claims = app.state.jwt_service.validar(raw_token)
    assert claims.valid
    assert claims.action == ArenaTokenAction.LOGIN
    assert claims.extra_data is not None
    assert claims.extra_data.get("tid") == expected_tid
    assert claims.extra_data.get("remember_me") is False


@pytest.mark.asyncio
async def test_login_token_sub_is_user_id(session: AsyncSession) -> None:
    """The issued LOGIN JWT sub claim must be the user's UUID, not email."""
    app = _build_arena_app(session)
    user = await _create_active_user(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/login",
            data={"email": "user@test.example", "password": "StrongPass1!"},
            follow_redirects=False,
        )

    raw_token = response.cookies["arena_access_token"]
    claims = app.state.jwt_service.validar(raw_token)
    assert claims.sub == user.id


@pytest.mark.asyncio
async def test_password_only_login_records_password_mode(session: AsyncSession) -> None:
    """Successful password-only login records login history as password."""
    app = _build_arena_app(session)
    user = await _create_active_user(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/login",
            data={"email": "user@test.example", "password": "StrongPass1!"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert await _login_history_modes(session, user.id) == ["password"]


# ---------------------------------------------------------------------------
# Login failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_wrong_password_redirects_without_cookie(session: AsyncSession) -> None:
    """Wrong password redirects to login without setting a session cookie."""
    app = _build_arena_app(session)
    await _create_active_user(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/login",
            data={"email": "user@test.example", "password": "WrongPassword1!"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "arena_access_token" not in response.cookies
    assert "/auth/login" in response.headers["location"]


@pytest.mark.asyncio
async def test_login_nonexistent_email_redirects_without_cookie(session: AsyncSession) -> None:
    """Unknown email redirects to login without setting a session cookie."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/login",
            data={"email": "nobody@test.example", "password": "StrongPass1!"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "arena_access_token" not in response.cookies
    assert "/auth/login" in response.headers["location"]


@pytest.mark.asyncio
async def test_login_inactive_unconfirmed_shows_resend_button(session: AsyncSession) -> None:
    """Inactive, unconfirmed account shows the resend-activation button inline."""
    app = _build_arena_app(session)
    await _create_inactive_user(session, email="inactive@test.example", email_confirmado=False)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/login",
            data={"email": "inactive@test.example", "password": "StrongPass1!"},
            follow_redirects=False,
        )

    assert response.status_code == 200
    assert "arena_access_token" not in response.cookies
    # The login template renders a resend-activation form when show_resend=True.
    assert "resend" in response.text.lower()


@pytest.mark.asyncio
async def test_login_inactive_confirmed_redirects_to_login(session: AsyncSession) -> None:
    """Inactive but confirmed (deactivated) account redirects to login with flash."""
    app = _build_arena_app(session)
    await _create_inactive_user(session, email="deactivated@test.example", email_confirmado=True)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/login",
            data={"email": "deactivated@test.example", "password": "StrongPass1!"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "arena_access_token" not in response.cookies
    assert "/auth/login" in response.headers["location"]


@pytest.mark.asyncio
async def test_login_minor_pending_parental_consent_shows_resend_button(
    session: AsyncSession,
) -> None:
    """Confirmed minor account pending consent can request a new consent email."""
    app = _build_arena_app(session)
    user = await _create_inactive_user(session, email="minor@test.example", email_confirmado=True)
    user.dta_nascimento = date(2010, 1, 1)
    user.email_responsavel_legal = "parent@test.example"
    user.consentimento_responsavel = False
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/login",
            data={"email": "minor@test.example", "password": "StrongPass1!"},
            follow_redirects=False,
        )

    assert response.status_code == 200
    assert "arena_access_token" not in response.cookies
    assert "Resend consent email" in response.text


@pytest.mark.asyncio
async def test_login_active_legacy_user_without_birth_date_requires_reconfirmation(
    session: AsyncSession,
) -> None:
    """Active legacy users missing date of birth are routed to regularisation."""
    app = _build_arena_app(session)
    user = await _create_active_user(session, email="legacy@test.example")
    user.dta_nascimento = None
    user.consentimento_responsavel = False
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/login",
            data={"email": "legacy@test.example", "password": "StrongPass1!"},
            follow_redirects=False,
        )

    assert response.status_code == 200
    assert "arena_access_token" not in response.cookies
    assert "Confirm date of birth" in response.text


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_without_cookie_is_idempotent(session: AsyncSession) -> None:
    """POST /auth/logout with no session cookie still redirects cleanly."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/auth/logout", follow_redirects=False)

    assert response.status_code == 303
    assert "/dashboard" in response.headers["location"]
    # A delete-cookie header should always be present for defence-in-depth.
    assert "arena_access_token" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_logout_with_valid_token_clears_cookie(session: AsyncSession) -> None:
    """POST /auth/logout with a valid cookie deletes the cookie and redirects."""
    app = _build_arena_app(session)
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
        response = await client.post("/auth/logout", follow_redirects=False)

    assert response.status_code == 303
    assert "/dashboard" in response.headers["location"]
    assert "arena_access_token" in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_logout_with_invalid_token_still_clears_cookie(session: AsyncSession) -> None:
    """POST /auth/logout with an invalid cookie still clears it and redirects."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", "not.a.valid.jwt")
        response = await client.post("/auth/logout", follow_redirects=False)

    assert response.status_code == 303
    assert "/dashboard" in response.headers["location"]
    assert "arena_access_token" in response.headers.get("set-cookie", "")
