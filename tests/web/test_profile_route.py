#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import logging
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from shared.services.geolocation import GeolocationDetails, GeolocationIP
from shared.services.imageprocessing_service import ImageProcessingService
from web.audio_upload_limits import DEFAULT_AUDIO_MAX_FILE_SIZE, MAX_AUDIO_FILE_SIZE
from web.models.site import Site
from web.models.users import UberAdmin, User, UserMedia
from web.routes.profile import router as profile_router
from web.routes.session import router as session_router
from web.routes.user_media import router as user_media_router
from web.services.authentication_service import AuthAction, AuthenticationService
from web.services.site_service import normalize_site_name_key
from web.services.user_media_service import process_audio_upload
from web.template_globals import register_template_globals

TEST_JWT_SECRET = "test-secret-key-for-tests-only-32bytes"

_AUDIO_SAMPLES = {
    "clip.mp3": (b"ID3\x04\x00\x00\x00\x00\x00\x00" + (b"\x00" * 64), "audio/mpeg"),
    "clip.ogg": (b"OggS" + (b"\x00" * 64), "audio/ogg"),
    "clip.wav": (b"RIFF" + (b"\x00" * 4) + b"WAVEfmt " + (b"\x00" * 32), "audio/wav"),
}


def test_user_table_does_not_map_legacy_photo_columns() -> None:
    legacy_columns = {"com_foto", "foto_base64", "avatar_base64", "foto_mime", "dta_foto"}

    assert legacy_columns.isdisjoint(User.__table__.columns.keys())


class _NoopGeo:
    def get_details_by_ip(self, ip_address: str | None) -> GeolocationDetails | None:
        return None


def _build_profile_app(session: AsyncSession) -> tuple[FastAPI, AuthenticationService]:
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    web_dir = Path(__file__).resolve().parents[2] / "web"
    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    templates = Jinja2Templates(directory=web_dir / "template")
    templates.env.globals["app_version"] = "test"
    register_template_globals(templates)
    # `_base.html` resolves the keepalive route on every authenticated page.
    app.include_router(session_router)
    setup_flash(templates)
    app.state.templates = templates

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
    app.state.image_service = ImageProcessingService()

    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/css", StaticFiles(directory=web_dir / "static" / "css"), name="static_css")
    app.mount("/static/js", StaticFiles(directory=web_dir / "static" / "js"), name="static_js")
    app.mount("/static/shared/js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")
    app.mount("/static/img", StaticFiles(directory=web_dir / "static" / "img"), name="static_img")

    @app.get("/uberadmin", name="uberadmin_dashboard")
    async def _uberadmin_dashboard() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/logout", name="logout")
    async def _logout() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/login", name="login_get")
    async def _login() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/c/{slug}", name="contest_dashboard")
    async def _contest_dashboard(slug: str) -> dict[str, str]:
        return {"slug": slug}

    @app.get("/c/{slug}/clock", name="contest_clock")
    async def _contest_clock(slug: str) -> dict[str, str]:
        return {"slug": slug}

    app.include_router(profile_router)
    app.include_router(user_media_router)
    return app, app.state.auth_service


async def _uberadmin_token(auth_service: AuthenticationService, session: AsyncSession, username: str) -> str:
    return await auth_service.uberadmin_login(username=username, password="TestPass1!", session=session)


async def _contest_user_token(
    auth_service: AuthenticationService,
    session: AsyncSession,
    username: str,
    contest_id: str,
) -> str:
    return await auth_service.user_login(
        username=username,
        password="TestPass1!",
        contest_id=contest_id,
        session=session,
    )


@pytest.mark.asyncio
async def test_uberadmin_profile_renders_without_photo_controls(session: AsyncSession, uberadmin: UberAdmin) -> None:
    app, auth_service = _build_profile_app(session)
    token = await _uberadmin_token(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get("/profile")

    assert response.status_code == 200
    assert 'card-header fw-semibold">Profile' in response.text
    assert "Change Password" in response.text
    assert 'card-header fw-semibold">Profile Photo' not in response.text
    assert "Back to dashboard" in response.text
    assert "/profile/email" in response.text


@pytest.mark.asyncio
async def test_uberadmin_profile_fullname_update_reuses_shared_validation(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    app, auth_service = _build_profile_app(session)
    token = await _uberadmin_token(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        success = await client.post(
            "/profile/fullname",
            data={"fullname": "Updated UberAdmin"},
            follow_redirects=False,
        )
        invalid = await client.post(
            "/profile/fullname",
            data={"fullname": "   "},
            follow_redirects=False,
        )

    await session.refresh(uberadmin)
    assert success.status_code == 303
    assert success.headers["location"] == "/profile"
    assert uberadmin.fullname == "Updated UberAdmin"
    assert invalid.status_code == 422
    assert "Display name cannot be empty." in invalid.text


@pytest.mark.asyncio
async def test_uberadmin_profile_password_update_reuses_shared_validation(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    app, auth_service = _build_profile_app(session)
    token = await _uberadmin_token(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        wrong_current = await client.post(
            "/profile/password",
            data={
                "current_password": "wrong",
                "new_password": "BetterPass2!",
                "confirm_password": "BetterPass2!",
            },
            follow_redirects=False,
        )
        mismatch = await client.post(
            "/profile/password",
            data={
                "current_password": "TestPass1!",
                "new_password": "BetterPass2!",
                "confirm_password": "MismatchPass2!",
            },
            follow_redirects=False,
        )
        success = await client.post(
            "/profile/password",
            data={
                "current_password": "TestPass1!",
                "new_password": "BetterPass2!",
                "confirm_password": "BetterPass2!",
            },
            follow_redirects=False,
        )

    await session.refresh(uberadmin)
    assert wrong_current.status_code == 422
    assert "Current password is incorrect." in wrong_current.text
    assert mismatch.status_code == 422
    assert "New passwords do not match." in mismatch.text
    assert success.status_code == 303
    assert success.headers["location"] == "/profile"

    second_token = await auth_service.uberadmin_login(
        username=uberadmin.username,
        password="BetterPass2!",
        session=session,
    )
    assert isinstance(second_token, str)


@pytest.mark.asyncio
async def test_contest_user_profile_shows_site_as_read_only_information(
    session: AsyncSession, running_contest, uberadmin, team_user
) -> None:
    site = Site(
        sitename="Campus A",
        sitename_normalized=normalize_site_name_key("Campus A"),
        contest_id=running_contest.id,
    )
    session.add(site)
    await session.flush()
    team_user.site_id = site.id
    await session.commit()

    app, auth_service = _build_profile_app(session)
    token = await _contest_user_token(auth_service, session, team_user.username, running_contest.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get("/profile")

    assert response.status_code == 200
    assert "Back to dashboard" not in response.text
    assert 'name="site_id"' not in response.text
    assert "Managed by contest administrators." in response.text
    assert "Campus A" in response.text
    assert 'data-photo-preview-id="profilePhotoPreview"' in response.text
    assert 'data-audio-preview-id="profileAudioPreview"' in response.text
    assert 'id="profilePhotoUnsaved"' in response.text
    assert 'id="profileAudioUnsaved"' in response.text
    assert "JPEG, PNG, or WebP" in response.text
    assert "up to 2048 × 2048 px" in response.text
    assert "MP3, OGG, or WAV · up to 2.0 MiB" in response.text


@pytest.mark.asyncio
async def test_uberadmin_profile_email_update_validation(session: AsyncSession, uberadmin: UberAdmin) -> None:
    app, auth_service = _build_profile_app(session)
    token = await _uberadmin_token(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        invalid = await client.post(
            "/profile/email",
            data={"email": "invalid-email"},
            follow_redirects=False,
        )
        success = await client.post(
            "/profile/email",
            data={"email": "updated.admin@example.com"},
            follow_redirects=False,
        )

    await session.refresh(uberadmin)
    assert invalid.status_code == 422
    assert "Invalid email address." in invalid.text
    assert success.status_code == 303
    assert success.headers["location"] == "/profile"
    assert uberadmin.email_normalizado == "updated.admin@example.com"


@pytest.mark.asyncio
async def test_contest_user_profile_email_can_be_set_and_cleared(
    session: AsyncSession, running_contest, team_user
) -> None:
    app, auth_service = _build_profile_app(session)
    token = await _contest_user_token(auth_service, session, team_user.username, running_contest.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        set_email = await client.post(
            "/profile/email",
            data={"email": "team.profile@example.com"},
            follow_redirects=False,
        )
        clear_email = await client.post(
            "/profile/email",
            data={"email": ""},
            follow_redirects=False,
        )

    await session.refresh(team_user)
    assert set_email.status_code == 303
    assert clear_email.status_code == 303
    assert team_user.email_normalizado is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("filename", "expected_mime"), [(name, sample[1]) for name, sample in _AUDIO_SAMPLES.items()])
async def test_audio_upload_validation_accepts_supported_signatures(filename: str, expected_mime: str) -> None:
    content, _ = _AUDIO_SAMPLES[filename]
    upload = UploadFile(filename=filename, file=BytesIO(content))

    result = await process_audio_upload(upload)

    assert result.content == content
    assert result.mime_type == expected_mime


@pytest.mark.asyncio
async def test_audio_upload_validation_rejects_invalid_and_oversized_files() -> None:
    invalid = UploadFile(filename="clip.mp3", file=BytesIO(b"not audio"))
    wav_header = b"RIFF" + (b"\x00" * 4) + b"WAVEfmt "
    boundary = UploadFile(
        filename="clip.wav",
        file=BytesIO(wav_header + (b"\x00" * (DEFAULT_AUDIO_MAX_FILE_SIZE - len(wav_header)))),
    )
    oversized = UploadFile(
        filename="clip.wav",
        file=BytesIO(b"RIFF" + (b"\x00" * DEFAULT_AUDIO_MAX_FILE_SIZE)),
    )

    boundary_result = await process_audio_upload(boundary)

    assert len(boundary_result.content) == DEFAULT_AUDIO_MAX_FILE_SIZE
    with pytest.raises(ValueError, match="not a recognized"):
        await process_audio_upload(invalid)
    with pytest.raises(ValueError, match="too large"):
        await process_audio_upload(oversized)


@pytest.mark.asyncio
async def test_audio_upload_validation_rejects_limit_above_hard_cap() -> None:
    """Programmatic callers cannot exceed the 5 MiB audio hard cap."""
    upload = UploadFile(filename="clip.wav", file=BytesIO(_AUDIO_SAMPLES["clip.wav"][0]))

    with pytest.raises(ValueError, match="between 1 byte and 5 MiB"):
        await process_audio_upload(upload, max_file_size=MAX_AUDIO_FILE_SIZE + 1)


@pytest.mark.asyncio
async def test_contest_user_can_upload_preview_and_remove_audio(
    session: AsyncSession, running_contest, team_user
) -> None:
    app, auth_service = _build_profile_app(session)
    token = await _contest_user_token(auth_service, session, team_user.username, running_contest.id)
    content, expected_mime = _AUDIO_SAMPLES["clip.mp3"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        uploaded = await client.post(
            f"/user/{team_user.id}/audio",
            files={"audio_clip": ("clip.mp3", content, "application/octet-stream")},
            follow_redirects=False,
        )
        preview = await client.get(f"/user/{team_user.id}/audio")
        profile = await client.get("/profile")
        removed = await client.post(
            f"/user/{team_user.id}/audio/remove",
            follow_redirects=False,
        )
        missing = await client.get(f"/user/{team_user.id}/audio")

    media = await session.get(UserMedia, team_user.id)
    assert uploaded.status_code == 303
    assert uploaded.headers["location"] == "/profile"
    assert preview.status_code == 200
    assert preview.content == content
    assert preview.headers["content-type"] == expected_mime
    assert "removeAudioModal" in profile.text
    assert removed.status_code == 303
    assert media is not None
    await session.refresh(media)
    assert media.audio_base64 is None
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_contest_user_photo_uses_users_media_and_keeps_avatar_fallback(
    session: AsyncSession, running_contest, team_user
) -> None:
    image_buffer = BytesIO()
    Image.new("RGB", (160, 100), "#336699").save(image_buffer, format="PNG")
    image_content = image_buffer.getvalue()
    app, auth_service = _build_profile_app(session)
    token = await _contest_user_token(auth_service, session, team_user.username, running_contest.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        fallback = await client.get(f"/user/{team_user.id}/avatar")
        uploaded = await client.post(
            f"/user/{team_user.id}/photo",
            files={"foto_cropada": ("photo.png", image_content, "image/png")},
            follow_redirects=False,
        )
        photo = await client.get(f"/user/{team_user.id}/photo")
        removed = await client.post(
            f"/user/{team_user.id}/photo/remove",
            follow_redirects=False,
        )
        restored_fallback = await client.get(f"/user/{team_user.id}/avatar")

    media = await session.get(UserMedia, team_user.id)
    assert fallback.headers["content-type"] == "image/svg+xml"
    assert uploaded.status_code == 303
    assert photo.headers["content-type"] == "image/png"
    assert removed.status_code == 303
    assert media is not None
    await session.refresh(media)
    assert media.com_foto is False
    assert restored_fallback.headers["content-type"] == "image/svg+xml"


@pytest.mark.asyncio
async def test_contest_admin_can_upload_audio_for_another_user(
    session: AsyncSession, running_contest, admin_user, team_user
) -> None:
    app, auth_service = _build_profile_app(session)
    token = await _contest_user_token(auth_service, session, admin_user.username, running_contest.id)
    content, _ = _AUDIO_SAMPLES["clip.ogg"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.post(
            f"/user/{team_user.id}/audio",
            data={"return_to": "admin_edit"},
            files={"audio_clip": ("clip.ogg", content, "audio/ogg")},
            follow_redirects=False,
        )

    media = await session.get(UserMedia, team_user.id)
    assert response.status_code == 303
    assert response.headers["location"] == f"/c/{running_contest.login_slug}/admin/users/{team_user.id}/edit"
    assert media is not None
    assert media.audio_mime == "audio/ogg"


@pytest.mark.asyncio
async def test_contest_admin_cannot_change_media_after_contest_ends(
    session: AsyncSession, running_contest, admin_user, team_user
) -> None:
    running_contest.start_time = datetime.now(UTC) - timedelta(hours=5)
    running_contest.duration_minutes = 60
    await session.commit()
    app, auth_service = _build_profile_app(session)
    token = await _contest_user_token(auth_service, session, admin_user.username, running_contest.id)
    content, _ = _AUDIO_SAMPLES["clip.wav"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.post(
            f"/user/{team_user.id}/audio",
            data={"return_to": "admin_edit"},
            files={"audio_clip": ("clip.wav", content, "audio/wav")},
            follow_redirects=False,
        )

    assert response.status_code == 403
    assert await session.get(UserMedia, team_user.id) is None
