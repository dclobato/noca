#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Bounded downloader and validator for Google OpenID Connect pictures.

Google's verified ``picture`` claim is presentation data, not trusted image
bytes. This service accepts only HTTPS URLs on Google-owned image hosts,
downloads them without redirects or environment proxies, enforces Arena's
configured byte limit while streaming, and delegates content validation and
avatar generation to the shared image-processing pipeline.
"""

from __future__ import annotations

import io
from urllib.parse import urlsplit

import httpx2
from fastapi import UploadFile

from shared.services.imageprocessing_service import (
    ImageProcessingError,
    ImageProcessingResult,
    ImageProcessingService,
)

_GOOGLE_IMAGE_HOST_SUFFIXES = ("googleusercontent.com", "ggpht.com")
_DOWNLOAD_TIMEOUT_SECONDS = 5.0
_MAX_PICTURE_URL_LENGTH = 2048


class GoogleAvatarError(Exception):
    """Raised when a Google picture cannot be downloaded or validated."""


class GoogleAvatarService:
    """Download and process Google profile pictures for local avatar caching."""

    def __init__(
        self,
        *,
        image_service: ImageProcessingService,
        max_file_size: int,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """Initialize the service with Arena's existing image limits.

        Args:
            image_service: Shared image validator and avatar generator.
            max_file_size: Maximum downloaded picture size in bytes.
            transport: Optional HTTP transport override for isolated tests.
        """
        self._image_service = image_service
        self._max_file_size = max_file_size
        self._transport = transport

    async def download(self, picture_url: str) -> ImageProcessingResult:
        """Download, validate, and resize one Google profile picture.

        Args:
            picture_url: Verified OpenID Connect ``picture`` claim.

        Returns:
            ImageProcessingResult: Validated image and generated avatar data.

        Raises:
            GoogleAvatarError: If the URL, response, or image is unsafe or invalid.
        """
        self._validate_picture_url(picture_url)
        try:
            image_bytes = await self._download_bytes(picture_url)
            upload = UploadFile(
                file=io.BytesIO(image_bytes),
                filename="google-profile-picture",
            )
            return await self._image_service.process_upload_image(
                upload=upload,
                max_file_size=self._max_file_size,
            )
        except (GoogleAvatarError, ImageProcessingError, ValueError) as exc:
            if isinstance(exc, GoogleAvatarError):
                raise
            raise GoogleAvatarError(str(exc)) from exc

    async def _download_bytes(self, picture_url: str) -> bytes:
        """Stream one picture into a bounded byte buffer."""
        headers = {"Accept": "image/avif,image/webp,image/png,image/jpeg"}
        try:
            async with (
                httpx2.AsyncClient(
                    timeout=_DOWNLOAD_TIMEOUT_SECONDS,
                    follow_redirects=False,
                    trust_env=False,
                    transport=self._transport,
                ) as client,
                client.stream("GET", picture_url, headers=headers) as response,
            ):
                response.raise_for_status()
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) > self._max_file_size:
                    raise GoogleAvatarError("Google profile picture exceeds Arena's image size limit.")

                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self._max_file_size:
                        raise GoogleAvatarError("Google profile picture exceeds Arena's image size limit.")
        except GoogleAvatarError:
            raise
        except (httpx2.HTTPError, ValueError) as exc:
            raise GoogleAvatarError("Google profile picture could not be downloaded.") from exc

        if not content:
            raise GoogleAvatarError("Google profile picture was empty.")
        return bytes(content)

    @staticmethod
    def _validate_picture_url(picture_url: str) -> None:
        """Reject URLs outside the narrow Google image-host boundary."""
        if not picture_url or len(picture_url) > _MAX_PICTURE_URL_LENGTH:
            raise GoogleAvatarError("Google did not provide a usable profile picture URL.")
        try:
            parsed = urlsplit(picture_url)
            hostname = (parsed.hostname or "").lower().rstrip(".")
            port = parsed.port
        except ValueError as exc:
            raise GoogleAvatarError("Google did not provide a usable profile picture URL.") from exc

        google_host = any(
            hostname == suffix or hostname.endswith(f".{suffix}") for suffix in _GOOGLE_IMAGE_HOST_SUFFIXES
        )
        if (
            parsed.scheme.lower() != "https"
            or not google_host
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
        ):
            raise GoogleAvatarError("Google did not provide a usable profile picture URL.")
