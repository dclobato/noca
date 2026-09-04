#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for bounded Google profile-picture downloads and validation."""

from __future__ import annotations

from io import BytesIO

import httpx2
import pytest
from PIL import Image

from arena.services.google_avatar_service import GoogleAvatarError, GoogleAvatarService
from shared.services.imageprocessing_service import ImageProcessingConfig, ImageProcessingService

_PICTURE_URL = "https://lh3.googleusercontent.com/a/profile-picture"


def _png_bytes(*, width: int = 48, height: int = 48) -> bytes:
    """Return a small valid PNG for downloader tests."""
    buffer = BytesIO()
    Image.new("RGB", (width, height), color=(66, 133, 244)).save(buffer, format="PNG")
    return buffer.getvalue()


def _service(handler: httpx2.MockTransport) -> GoogleAvatarService:
    """Build the service with deterministic image limits and HTTP transport."""
    image_service = ImageProcessingService(
        config=ImageProcessingConfig(
            avatar_size=16,
            max_file_size=1024,
            max_width=128,
            max_height=128,
        )
    )
    return GoogleAvatarService(
        image_service=image_service,
        max_file_size=1024,
        transport=handler,
    )


@pytest.mark.asyncio
async def test_download_validates_and_resizes_a_google_picture() -> None:
    """A valid Google-hosted image passes through the shared image pipeline."""
    content = _png_bytes()

    def _handler(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == _PICTURE_URL
        assert request.headers["Accept"].startswith("image/")
        return httpx2.Response(200, content=content, headers={"Content-Type": "image/png"})

    result = await _service(httpx2.MockTransport(_handler)).download(_PICTURE_URL)

    assert result.mime_type == "image/png"
    assert result.original_dimensions == (48, 48)
    assert result.avatar_dimensions == (16, 16)
    assert result.avatar_base64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "picture_url",
    [
        "http://lh3.googleusercontent.com/a/avatar",
        "https://example.com/avatar.png",
        "https://googleusercontent.com.example.com/avatar.png",
        "https://user@lh3.googleusercontent.com/avatar.png",
        "https://lh3.googleusercontent.com:8443/avatar.png",
    ],
)
async def test_download_rejects_urls_outside_google_image_hosts(picture_url: str) -> None:
    """The picture claim cannot turn Arena into a general-purpose URL fetcher."""

    def _unexpected(_request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("unsafe URL reached the HTTP transport")

    with pytest.raises(GoogleAvatarError, match="usable profile picture URL"):
        await _service(httpx2.MockTransport(_unexpected)).download(picture_url)


@pytest.mark.asyncio
async def test_download_does_not_follow_redirects() -> None:
    """A Google-hosted URL cannot redirect the downloader outside its trust boundary."""

    def _handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"Location": "http://127.0.0.1/private"})

    with pytest.raises(GoogleAvatarError, match="could not be downloaded"):
        await _service(httpx2.MockTransport(_handler)).download(_PICTURE_URL)


@pytest.mark.asyncio
async def test_download_rejects_an_oversized_content_length_before_reading() -> None:
    """A declared response above the image limit is refused immediately."""

    def _handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            content=_png_bytes(),
            headers={"Content-Length": "2048"},
        )

    with pytest.raises(GoogleAvatarError, match="image size limit"):
        await _service(httpx2.MockTransport(_handler)).download(_PICTURE_URL)


@pytest.mark.asyncio
async def test_download_rejects_content_that_is_not_an_image() -> None:
    """HTTP success and an image-looking URL do not bypass signature validation."""

    def _handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"not an image")

    with pytest.raises(GoogleAvatarError, match="File type could not be identified"):
        await _service(httpx2.MockTransport(_handler)).download(_PICTURE_URL)
