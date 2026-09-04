#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for public Arena user image endpoints."""

import logging
from base64 import b64encode
from datetime import UTC, date, datetime
from io import BytesIO

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_users  # noqa: F401
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_user_google_identity import ArenaUserGoogleIdentity
from arena.models.arena_users import ArenaUser
from arena.routes.auth import router as arena_auth_router
from arena.routes.auth_password import router as arena_auth_password_router
from arena.routes.auth_signup import router as arena_auth_signup_router
from arena.routes.users import router as arena_users_router
from arena.services.token_service import ArenaTokenAction
from shared.enumerations import ArenaRole
from shared.services.email_service import EmailConfig, EmailService
from shared.services.imageprocessing_service import ImageProcessingConfig, ImageProcessingService
from tests.arena.conftest import attach_reputation_services, install_arena_templates, mount_arena_base_routes

TEST_JWT_SECRET = "test-secret-key-for-arena-image-tests-only-32bytes"


def _build_arena_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena app for user image route tests."""
    app = FastAPI()
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    install_arena_templates(app)
    mount_arena_base_routes(app)

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
    attach_reputation_services(app)

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


def _login_token(app: FastAPI, user: ArenaUser) -> str:
    """Issue a valid Arena login token for an image-route test user."""
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )


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


@pytest.mark.asyncio
async def test_avatar_route_prefers_selected_google_cache_without_replacing_arena_photo(
    session: AsyncSession,
) -> None:
    """Google selection changes the canonical avatar while preserving the upload."""
    app = _build_arena_app(session)
    user = ArenaUser(
        nome="Avatar Source User",
        email_normalizado="avatar-source@test.example",
        password_hash="pbkdf2:sha256:1000000$avatar$testhash",
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        aceitou_termos_privacidade=True,
        com_foto=False,
        usa_2fa=False,
        precisa_trocar_senha=False,
        session_version=0,
    )
    arena_photo = app.state.image_service.process_base64(b64encode(_png_portrait_bytes()).decode("ascii"))
    google_picture = app.state.image_service.process_base64(b64encode(_png_upload_bytes()).decode("ascii"))
    user.apply_processed_photo(
        foto_base64=arena_photo.imagem_base64,
        avatar_base64=arena_photo.avatar_base64,
        mime_type=arena_photo.mime_type,
    )
    session.add(user)
    await session.flush()
    identity = ArenaUserGoogleIdentity(
        user_id=user.id,
        google_sub="avatar-source-google-sub",
        google_email="avatar-source@gmail.example",
        google_email_verified=True,
        google_picture_url="https://lh3.googleusercontent.com/a/avatar-source",
        google_avatar_base64=google_picture.avatar_base64,
        google_avatar_mime=google_picture.mime_type,
        google_avatar_refreshed_at=datetime.now(UTC),
        use_google_avatar=True,
        linked_at=datetime.now(UTC),
    )
    session.add(identity)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        google_response = await client.get(f"/user/{user.id}/avatar?v={user.avatar_revision}")
        client.cookies.set("arena_access_token", _login_token(app, user))
        upload_response = await client.post(
            "/user/profile/photo",
            files={"foto_cropada": ("replacement.png", _png_portrait_bytes(), "image/png")},
            follow_redirects=False,
        )
        arena_response = await client.get(f"/user/{user.id}/avatar?v={user.avatar_revision}")

    await session.refresh(identity)
    await session.refresh(user)
    assert upload_response.status_code == 303
    assert identity.use_google_avatar is False
    assert google_response.content != arena_response.content
    assert _image_size(google_response.content) == (16, 8)
    assert _image_size(arena_response.content) == (10, 15)
    assert user.com_foto is True


@pytest.mark.asyncio
async def test_unversioned_avatar_revalidates_with_a_304(session: AsyncSession) -> None:
    """An unversioned URL must revalidate -- and now it can, without the image.

    The ranking page issued one full avatar download per row per view because the
    route answered its unversioned URLs with ``must-revalidate`` while the shared
    helper emitted no ``ETag`` (#199). With a content tag, the revalidation is a
    header exchange, and the ``must-revalidate`` policy survives on the ``304``.
    """
    app = _build_arena_app(session)
    user = ArenaUser(
        nome="Avatar Etag User",
        email_normalizado="avatar-etag@test.example",
        password_hash="pbkdf2:sha256:1000000$avatar$testhash",
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        com_foto=False,
        usa_2fa=False,
        precisa_trocar_senha=False,
        session_version=0,
    )
    session.add(user)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        first = await client.get(f"/user/{user.id}/avatar")
        again = await client.get(f"/user/{user.id}/avatar", headers={"If-None-Match": first.headers["etag"]})
        stale = await client.get(f"/user/{user.id}/avatar", headers={"If-None-Match": '"stale"'})

    assert first.status_code == 200
    assert "must-revalidate" in first.headers["cache-control"]
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["etag"] == first.headers["etag"]
    assert "must-revalidate" in again.headers["cache-control"]
    assert stale.status_code == 200


@pytest.mark.asyncio
async def test_avatar_route_only_long_caches_the_current_revision(session: AsyncSession) -> None:
    """Unversioned and stale URLs revalidate while the current revision is cacheable."""
    app = _build_arena_app(session)
    user = ArenaUser(
        nome="Avatar Cache User",
        email_normalizado="avatar-cache@test.example",
        password_hash="pbkdf2:sha256:1000000$avatar$testhash",
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        com_foto=False,
        usa_2fa=False,
        precisa_trocar_senha=False,
        session_version=0,
    )
    session.add(user)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        current = await client.get(f"/user/{user.id}/avatar?v={user.avatar_revision}")
        stale = await client.get(f"/user/{user.id}/avatar?v=stale")
        unversioned = await client.get(f"/user/{user.id}/avatar")

    assert current.headers["cache-control"] == "public, max-age=3600"
    assert stale.headers["cache-control"] == "public, max-age=0, must-revalidate"
    assert unversioned.headers["cache-control"] == "public, max-age=0, must-revalidate"
