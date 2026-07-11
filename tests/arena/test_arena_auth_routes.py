#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for Arena signup, activation, and password reset flows."""

import logging
from io import BytesIO
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_users  # noqa: F401
from arena.models.arena_users import ArenaUser
from arena.routes.auth import router as arena_auth_router
from arena.routes.auth_password import router as arena_auth_password_router
from arena.routes.auth_signup import router as arena_auth_signup_router
from arena.routes.help import router as arena_help_router
from arena.routes.legal import router as arena_legal_router
from arena.routes.ranking import router as arena_ranking_router
from arena.routes.root import router as arena_root_router
from arena.services.token_service import ArenaTokenAction
from shared.db_schema import security_events
from shared.services.email_service import EmailConfig, EmailService
from shared.services.imageprocessing_service import ImageProcessingConfig, ImageProcessingService

TEST_JWT_SECRET = "test-secret-key-for-arena-tests-only-32bytes"


def _build_arena_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena app for auth route tests."""
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
                "SECRET_KEY": TEST_JWT_SECRET,
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

    shared_dir = arena_dir.parent / "shared"
    app.mount("/static/css", StaticFiles(directory=arena_dir / "static" / "css"), name="arena_static_css")
    app.mount("/static/js", StaticFiles(directory=arena_dir / "static" / "js"), name="arena_static_js")
    app.mount("/static/img", StaticFiles(directory=arena_dir / "static" / "img"), name="arena_static_img")
    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/shared-js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")

    @app.get("/problems", name="arena_problem_list")
    async def _problem_list() -> Response:
        return Response("problems")

    @app.get("/classes", name="arena_classes_index")
    async def _classes_index() -> Response:
        return Response("classes")

    @app.get("/classes/registered", name="arena_classes_registered")
    async def _classes_registered() -> Response:
        return Response("classes registered")

    @app.get("/classes/open", name="arena_classes_open")
    async def _classes_open() -> Response:
        return Response("classes open")

    @app.get("/classes/manage", name="arena_classes_manage")
    async def _classes_manage() -> Response:
        return Response("classes manage")

    @app.get("/status", name="arena_status")
    async def _status() -> Response:
        return Response("status")

    @app.get("/admin/problems", name="arena_admin_problem_list")
    async def _admin_problems() -> Response:
        return Response("problems")

    @app.get("/admin/dashboard", name="arena_admin_dashboard")
    async def _admin_dashboard_stub() -> Response:
        return Response("dashboard")

    @app.get("/admin/categories", name="arena_admin_category_list")
    async def _admin_categories() -> Response:
        return Response("categories")

    @app.get("/admin/affiliations", name="arena_admin_affiliation_list")
    async def _admin_affiliations() -> Response:
        return Response("affiliations")

    app.include_router(arena_root_router)
    app.include_router(arena_auth_router)
    app.include_router(arena_auth_signup_router)
    app.include_router(arena_auth_password_router)
    app.include_router(arena_legal_router)
    app.include_router(arena_help_router)
    app.include_router(arena_ranking_router)
    return app


def _sent_email_text(app: FastAPI, index: int = -1) -> str:
    """Return text body from the mock email provider."""
    provider = app.state.email_service.provider
    sent = cast(Any, provider).get_sent_emails()
    return str(sent[index]["text_body"])


async def _user_by_email(session: AsyncSession, email: str) -> ArenaUser | None:
    """Fetch an Arena user by normalized email."""
    result = await session.execute(select(ArenaUser).where(ArenaUser.email_normalizado == email))
    return result.scalar_one_or_none()


async def _security_event_types_for_user(session: AsyncSession, user_id: str) -> set[str]:
    """Return recorded security-event types for an Arena user."""
    result = await session.execute(
        select(security_events.c.event_type).where(
            security_events.c.module == "arena",
            security_events.c.actor_user_id == user_id,
        )
    )
    return set(result.scalars())


def _png_upload_bytes() -> bytes:
    """Build a tiny valid PNG image for upload tests."""
    buffer = BytesIO()
    Image.new("RGB", (16, 16), color=(0, 107, 33)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_signup_page_renders_with_flash_macro_context(session: AsyncSession) -> None:
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/signup")

    assert response.status_code == 200
    assert "Create Account" in response.text
    assert "Password must be at least" in response.text


@pytest.mark.asyncio
async def test_legal_document_routes_render_markdown_sources(session: AsyncSession) -> None:
    """Legal pages must expose the copied markdown documents."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        terms_response = await client.get("/legal/terms")
        privacy_response = await client.get("/legal/privacy")

    assert terms_response.status_code == 200
    assert "Terms of Service" in terms_response.text
    assert "legal-markdown-src" in terms_response.text
    assert "Termos e Condições de Uso" in terms_response.text

    assert privacy_response.status_code == 200
    assert "Privacy Policy" in privacy_response.text
    assert "legal-markdown-src" in privacy_response.text
    assert "Política de Privacidade" in privacy_response.text


@pytest.mark.asyncio
async def test_signup_creates_user_and_sends_activation_email(session: AsyncSession) -> None:
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/signup",
            data={
                "full_name": "Arena User",
                "date_of_birth": "2000-01-02",
                "email": "User@Test.Example",
                "password": "StrongPass1!",
                "confirm_password": "StrongPass1!",
                "terms": "on",
            },
            follow_redirects=False,
        )

    user = await _user_by_email(session, "user@test.example")
    assert response.status_code == 303
    assert response.headers["location"] == "http://testserver/auth/login"
    assert user is not None
    assert user.nome == "Arena User"
    assert user.ativo is False
    assert user.email_confirmado is False
    assert user.consentimento_responsavel is True
    assert user.aceitou_termos_privacidade is True
    assert user.dta_aceitacao_termos_privacidade is not None
    assert "auth/activate?token=" in _sent_email_text(app)
    assert await _security_event_types_for_user(session, user.id) == {
        "account_activation_email_sent",
        "account_signup_created",
    }


@pytest.mark.asyncio
async def test_signup_rejects_missing_terms(session: AsyncSession) -> None:
    """Signup without the terms checkbox must re-render the form and create no user."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/signup",
            data={
                "full_name": "Arena User",
                "date_of_birth": "2000-01-02",
                "email": "noterms@test.example",
                "password": "StrongPass1!",
                "confirm_password": "StrongPass1!",
                # terms field intentionally omitted
            },
            follow_redirects=False,
        )

    user = await _user_by_email(session, "noterms@test.example")
    assert response.status_code == 422  # re-renders form with validation error
    assert user is None  # no row created


@pytest.mark.asyncio
async def test_signup_rejects_user_younger_than_13(session: AsyncSession) -> None:
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/signup",
            data={
                "full_name": "Too Young",
                "date_of_birth": "2020-01-02",
                "email": "young@test.example",
                "password": "StrongPass1!",
                "confirm_password": "StrongPass1!",
                "terms": "on",
            },
            follow_redirects=False,
        )

    assert response.status_code == 422
    assert await _user_by_email(session, "young@test.example") is None


@pytest.mark.asyncio
async def test_signup_minor_requires_parental_email(session: AsyncSession) -> None:
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/signup",
            data={
                "full_name": "Minor User",
                "date_of_birth": "2010-01-02",
                "email": "minor@test.example",
                "password": "StrongPass1!",
                "confirm_password": "StrongPass1!",
                "terms": "on",
            },
            follow_redirects=False,
        )

    assert response.status_code == 422
    assert await _user_by_email(session, "minor@test.example") is None


@pytest.mark.asyncio
async def test_signup_minor_sends_activation_and_parental_consent_emails(
    session: AsyncSession,
) -> None:
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/signup",
            data={
                "full_name": "Minor User",
                "date_of_birth": "2010-01-02",
                "email": "minor@test.example",
                "email_responsavel_legal": "Parent@Test.Example",
                "password": "StrongPass1!",
                "confirm_password": "StrongPass1!",
                "terms": "on",
            },
            follow_redirects=False,
        )

    user = await _user_by_email(session, "minor@test.example")
    provider = cast(Any, app.state.email_service.provider)
    sent = provider.get_sent_emails()
    assert response.status_code == 303
    assert user is not None
    assert user.email_responsavel_legal == "parent@test.example"
    assert user.consentimento_responsavel is False
    assert user.ativo is False
    assert len(sent) == 2
    assert "auth/activate?token=" in sent[0]["text_body"]
    assert "auth/parental-consent?token=" in sent[1]["text_body"]
    assert await _security_event_types_for_user(session, user.id) == {
        "account_activation_email_sent",
        "account_signup_created",
        "parental_consent_email_sent",
    }


@pytest.mark.asyncio
async def test_signup_processes_profile_photo_upload(session: AsyncSession) -> None:
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/signup",
            data={
                "full_name": "Photo User",
                "date_of_birth": "2000-01-02",
                "email": "photo@test.example",
                "password": "StrongPass1!",
                "confirm_password": "StrongPass1!",
                "terms": "on",
            },
            files={"profile_photo": ("profile.png", _png_upload_bytes(), "image/png")},
            follow_redirects=False,
        )

    user = await _user_by_email(session, "photo@test.example")
    assert response.status_code == 303
    assert user is not None
    assert user.com_foto is True
    assert user.foto_base64
    assert user.avatar_base64
    assert user.foto_mime == "image/png"


@pytest.mark.asyncio
async def test_signup_rejects_mismatched_passwords(session: AsyncSession) -> None:
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/signup",
            data={
                "full_name": "Arena User",
                "date_of_birth": "2000-01-02",
                "email": "user@test.example",
                "password": "StrongPass1!",
                "confirm_password": "OtherPass1!",
                "terms": "on",
            },
            follow_redirects=False,
        )

    assert response.status_code == 422
    assert 'value="Arena User"' in response.text
    assert 'value="2000-01-02"' in response.text
    assert 'value="user@test.example"' in response.text
    assert "checked" in response.text
    assert await _user_by_email(session, "user@test.example") is None


@pytest.mark.asyncio
async def test_signup_preserves_safe_fields_when_terms_missing(session: AsyncSession) -> None:
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/signup",
            data={
                "full_name": "Arena User",
                "date_of_birth": "2000-01-02",
                "email": "user@test.example",
                "password": "StrongPass1!",
                "confirm_password": "StrongPass1!",
            },
            follow_redirects=False,
        )

    assert response.status_code == 422
    assert 'value="Arena User"' in response.text
    assert 'value="2000-01-02"' in response.text
    assert 'value="user@test.example"' in response.text
    assert 'id="password"' in response.text
    assert 'value="StrongPass1!"' not in response.text
    assert await _user_by_email(session, "user@test.example") is None


@pytest.mark.asyncio
async def test_activation_requires_validate_email_action(session: AsyncSession) -> None:
    app = _build_arena_app(session)
    wrong_token = app.state.jwt_service.criar(
        action=ArenaTokenAction.RESET_PASSWORD,
        sub="user@test.example",
        expires_in=3600,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/auth/activate?token={wrong_token}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "http://testserver/auth/login"


@pytest.mark.asyncio
async def test_activation_confirms_email_and_activates_account(session: AsyncSession) -> None:
    app = _build_arena_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post(
            "/auth/signup",
            data={
                "full_name": "Arena User",
                "date_of_birth": "2000-01-02",
                "email": "user@test.example",
                "password": "StrongPass1!",
                "confirm_password": "StrongPass1!",
                "terms": "on",
            },
        )
        token = _sent_email_text(app).split("token=", 1)[1].splitlines()[0]
        response = await client.get(f"/auth/activate?token={token}", follow_redirects=False)

    user = await _user_by_email(session, "user@test.example")
    assert response.status_code == 303
    assert user is not None
    assert user.email_confirmado is True
    assert user.ativo is True
    event_types = await _security_event_types_for_user(session, user.id)
    assert "email_confirmed" in event_types
    assert "account_activated" in event_types


@pytest.mark.asyncio
async def test_minor_account_activates_only_after_email_and_parental_consent(
    session: AsyncSession,
) -> None:
    app = _build_arena_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post(
            "/auth/signup",
            data={
                "full_name": "Minor User",
                "date_of_birth": "2010-01-02",
                "email": "minor@test.example",
                "email_responsavel_legal": "parent@test.example",
                "password": "StrongPass1!",
                "confirm_password": "StrongPass1!",
                "terms": "on",
            },
        )
        activation_token = _sent_email_text(app, 0).split("token=", 1)[1].splitlines()[0]
        parental_token = _sent_email_text(app, 1).split("token=", 1)[1].splitlines()[0]

        consent_response = await client.get(
            f"/auth/parental-consent?token={parental_token}",
            follow_redirects=False,
        )
        after_consent = await _user_by_email(session, "minor@test.example")
        assert after_consent is not None
        assert after_consent.consentimento_responsavel is True
        assert after_consent.ativo is False

        activation_response = await client.get(f"/auth/activate?token={activation_token}", follow_redirects=False)

    await session.refresh(after_consent)
    user = after_consent
    assert consent_response.status_code == 303
    assert activation_response.status_code == 303
    assert user is not None
    assert user.email_confirmado is True
    assert user.consentimento_responsavel is True
    assert user.ativo is True
    event_types = await _security_event_types_for_user(session, user.id)
    assert "email_confirmed" in event_types
    assert "parental_consent_confirmed" in event_types
    assert "account_activated" in event_types


@pytest.mark.asyncio
async def test_password_reset_request_uses_generic_success(session: AsyncSession) -> None:
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/auth/password-reset",
            data={"email": "missing@test.example"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "http://testserver/auth/login"
    assert cast(Any, app.state.email_service.provider).get_sent_emails() == []


@pytest.mark.asyncio
async def test_password_reset_changes_password_for_valid_token(session: AsyncSession) -> None:
    app = _build_arena_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post(
            "/auth/signup",
            data={
                "full_name": "Arena User",
                "date_of_birth": "2000-01-02",
                "email": "user@test.example",
                "password": "StrongPass1!",
                "confirm_password": "StrongPass1!",
                "terms": "on",
            },
        )
        await client.post("/auth/password-reset", data={"email": "user@test.example"})
        token = _sent_email_text(app).split("token=", 1)[1].splitlines()[0]
        response = await client.post(
            "/auth/password-reset",
            data={
                "token": token,
                "password": "NewStrongPass1!",
                "confirm_password": "NewStrongPass1!",
            },
            follow_redirects=False,
        )

    user = await _user_by_email(session, "user@test.example")
    assert response.status_code == 303
    assert user is not None
    assert user.check_password("NewStrongPass1!") is True
