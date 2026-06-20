#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for public Arena user image endpoints."""

import logging
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
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
from arena.routes.users import router as arena_users_router
from arena.services.token_service import ArenaTokenAction
from shared.services.email_service import EmailConfig, EmailService
from shared.services.imageprocessing_service import ImageProcessingConfig, ImageProcessingService

TEST_JWT_SECRET = "test-secret-key-for-arena-image-tests-only-32bytes"


def _build_arena_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena app for user image route tests."""
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
        config=ImageProcessingConfig(avatar_size=16, max_file_size=2 * 1024 * 1024),
        logger=logging.getLogger(__name__),
    )

    app.mount("/static/css", StaticFiles(directory=arena_dir / "static" / "css"), name="arena_static_css")
    app.mount("/static/js", StaticFiles(directory=arena_dir / "static" / "js"), name="arena_static_js")
    app.mount("/static/img", StaticFiles(directory=arena_dir / "static" / "img"), name="arena_static_img")
    app.include_router(arena_auth_router)
    app.include_router(arena_auth_signup_router)
    app.include_router(arena_auth_password_router)
    app.include_router(arena_users_router)
    return app


def _png_upload_bytes() -> bytes:
    """Build a valid PNG image for upload tests."""
    buffer = BytesIO()
    Image.new("RGB", (64, 32), color=(0, 107, 33)).save(buffer, format="PNG")
    return buffer.getvalue()


def _png_portrait_bytes() -> bytes:
    """Build a valid 2:3 portrait PNG image simulating a client-side crop result."""
    buffer = BytesIO()
    Image.new("RGB", (30, 45), color=(0, 107, 33)).save(buffer, format="PNG")
    return buffer.getvalue()


async def _user_by_email(session: AsyncSession, email: str) -> ArenaUser | None:
    """Fetch an Arena user by normalized email."""
    result = await session.execute(select(ArenaUser).where(ArenaUser.email_normalizado == email))
    return result.scalar_one_or_none()


def _image_size(content: bytes) -> tuple[int, int]:
    """Return image dimensions from raw bytes."""
    with Image.open(BytesIO(content)) as image:
        return image.size


@pytest.mark.asyncio
async def test_public_user_image_routes_serve_signup_photo_and_avatar(
    session: AsyncSession,
) -> None:
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        signup_response = await client.post(
            "/auth/signup",
            data={
                "full_name": "Photo User",
                "date_of_birth": "2000-01-02",
                "email": "photo-routes@test.example",
                "password": "StrongPass1!",
                "confirm_password": "StrongPass1!",
                "terms": "on",
            },
            files={"foto_cropada": ("profile.png", _png_portrait_bytes(), "image/png")},
            follow_redirects=False,
        )
        user = await _user_by_email(session, "photo-routes@test.example")
        assert user is not None

        photo_response = await client.get(f"/user/{user.id}/photo")
        avatar_response = await client.get(f"/user/{user.id}/avatar")

    assert signup_response.status_code == 303
    assert photo_response.status_code == 200
    assert avatar_response.status_code == 200
    assert photo_response.headers["content-type"] == "image/png"
    assert avatar_response.headers["content-type"] == "image/png"
    # 30x45 portrait (2:3) stays unchanged after server-side crop; avatar fits 16px box
    assert _image_size(photo_response.content) == (30, 45)
    assert _image_size(avatar_response.content) == (10, 15)


@pytest.mark.asyncio
async def test_public_user_image_routes_return_404_for_missing_user(
    session: AsyncSession,
) -> None:
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/user/missing-user/photo")

    assert response.status_code == 404
