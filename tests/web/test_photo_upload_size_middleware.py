#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for fail-fast Web media upload size enforcement."""

from typing import Annotated

from fastapi import FastAPI, File, Request, UploadFile
from fastapi_flash import FlashService
from httpx import ASGITransport, AsyncClient
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from shared.services.multipart_file_size import (
    MultipartFileSizeLimitMiddleware,
    MultipartFileSizeRule,
)
from web.error_handlers import http_exception_response


def _build_app(
    max_file_size: int,
    audio_max_file_size: int = 32,
) -> tuple[FastAPI, dict[str, bool]]:
    """Build a minimal app with independently protected media routes."""
    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, http_exception_response)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")
    app.add_middleware(
        MultipartFileSizeLimitMiddleware,
        rules=(
            MultipartFileSizeRule(
                path_pattern=r"^/user/[^/]+/photo$",
                max_file_size=max_file_size,
                label="Photo",
            ),
            MultipartFileSizeRule(
                path_pattern=r"^/user/[^/]+/audio$",
                max_file_size=audio_max_file_size,
                label="Audio",
            ),
        ),
    )
    route_called = {"photo": False, "audio": False}

    @app.get("/profile")
    async def profile(request: Request) -> dict[str, list[tuple[str, str]]]:
        """Expose flashed messages for redirect behavior assertions."""
        messages = FlashService(request).get_flashed_messages(with_categories=True)
        return {"messages": messages}

    @app.post("/user/{user_id}/photo")
    async def photo_upload(
        user_id: str,
        foto_cropada: Annotated[UploadFile, File()],
    ) -> dict[str, int | str]:
        """Return the accepted photo size."""
        route_called["photo"] = True
        content = await foto_cropada.read()
        return {"user_id": user_id, "size": len(content)}

    @app.post("/user/{user_id}/audio")
    async def audio_upload(
        user_id: str,
        audio_clip: Annotated[UploadFile, File()],
    ) -> dict[str, int | str]:
        """Return the accepted audio size."""
        route_called["audio"] = True
        content = await audio_clip.read()
        return {"user_id": user_id, "size": len(content)}

    return app, route_called


async def test_accepts_photo_at_exact_file_size_limit() -> None:
    """Multipart framing does not reduce the configured file-byte ceiling."""
    app, route_called = _build_app(max_file_size=16)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/user/user-1/photo",
            files={"foto_cropada": ("photo.png", b"x" * 16, "image/png")},
        )

    assert response.status_code == 200
    assert response.json()["size"] == 16
    assert route_called["photo"] is True


async def test_rejects_photo_as_soon_as_file_exceeds_limit() -> None:
    """An oversized photo returns 413 before the route is called."""
    app, route_called = _build_app(max_file_size=16)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/user/user-1/photo",
            files={"foto_cropada": ("photo.png", b"x" * 17, "image/png")},
        )

    assert response.status_code == 413
    assert response.json()["detail"] == ("Photo file too large. Maximum allowed: 0.0 MB.")
    assert route_called["photo"] is False


async def test_counts_all_file_parts_to_prevent_limit_bypass() -> None:
    """Multiple file fields cannot bypass the aggregate photo upload ceiling."""
    app, route_called = _build_app(max_file_size=16)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/user/user-1/photo",
            files=[
                ("foto_cropada", ("photo.png", b"x" * 10, "image/png")),
                ("ignored", ("extra.png", b"x" * 7, "image/png")),
            ],
        )

    assert response.status_code == 413
    assert route_called["photo"] is False


async def test_does_not_apply_photo_limit_to_audio_route() -> None:
    """Audio uses its dedicated limit instead of the photo limit."""
    app, route_called = _build_app(max_file_size=16, audio_max_file_size=32)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/user/user-1/audio",
            files={"audio_clip": ("clip.wav", b"x" * 17, "audio/wav")},
        )

    assert response.status_code == 200
    assert response.json()["size"] == 17
    assert route_called["audio"] is True


async def test_rejects_audio_as_soon_as_file_exceeds_limit() -> None:
    """An oversized audio file returns 413 before the route is called."""
    app, route_called = _build_app(max_file_size=16, audio_max_file_size=16)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/user/user-1/audio",
            files={"audio_clip": ("clip.wav", b"x" * 17, "audio/wav")},
        )

    assert response.status_code == 413
    assert response.json()["detail"] == ("Audio file too large. Maximum allowed: 0.0 MB.")
    assert route_called["audio"] is False


async def test_browser_audio_upload_redirects_back_with_warning() -> None:
    """Oversized browser audio uploads return with a warning flash."""
    app, route_called = _build_app(max_file_size=16, audio_max_file_size=16)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/user/user-1/audio",
            files={"audio_clip": ("clip.wav", b"x" * 17, "audio/wav")},
            headers={
                "accept": "text/html",
                "referer": "http://testserver/profile?tab=media",
            },
        )
        profile_response = await client.get(response.headers["location"])

    assert response.status_code == 303
    assert response.headers["location"] == "/profile?tab=media"
    messages = profile_response.json()["messages"]
    assert messages == [["warning", "Audio file too large. Maximum allowed: 0.0 MB."]]
    assert route_called["audio"] is False


async def test_browser_upload_redirects_back_with_warning() -> None:
    """HTML form submissions return to their page with a warning flash."""
    app, route_called = _build_app(max_file_size=16)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/user/user-1/photo",
            files={"foto_cropada": ("photo.png", b"x" * 17, "image/png")},
            headers={
                "accept": "text/html",
                "referer": "http://testserver/profile?tab=media",
            },
        )
        profile_response = await client.get(response.headers["location"])

    assert response.status_code == 303
    assert response.headers["location"] == "/profile?tab=media"
    messages = profile_response.json()["messages"]
    assert messages == [["warning", "Photo file too large. Maximum allowed: 0.0 MB."]]
    assert route_called["photo"] is False


async def test_browser_upload_rejects_external_return_url() -> None:
    """An external Referer cannot turn the warning redirect into an open redirect."""
    app, _ = _build_app(max_file_size=16)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/user/user-1/photo",
            files={"foto_cropada": ("photo.png", b"x" * 17, "image/png")},
            headers={
                "accept": "text/html",
                "referer": "https://attacker.example/steal",
            },
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/profile"
