#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for content-based image type enforcement."""

from __future__ import annotations

import io
from base64 import b64encode

import pytest
from fastapi import UploadFile
from PIL import Image
from starlette.datastructures import Headers

from shared.services.imageprocessing_service import (
    MAX_IMAGE_FILE_SIZE,
    ImageProcessingConfig,
    ImageProcessingError,
    ImageProcessingService,
)


def test_image_processing_config_uses_shared_file_size_hard_limit() -> None:
    """The image service retains the shared 5 MiB upload hard limit."""
    assert MAX_IMAGE_FILE_SIZE == 5 * 1024 * 1024
    assert ImageProcessingConfig().max_file_size == MAX_IMAGE_FILE_SIZE


def _image_bytes(image_format: str) -> bytes:
    """Return a small image encoded in the requested format."""
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format=image_format)
    return buffer.getvalue()


def _upload(content: bytes, declared_mime: str = "application/octet-stream") -> UploadFile:
    """Build an upload with an independently controlled MIME declaration."""
    return UploadFile(
        file=io.BytesIO(content),
        filename="image.bin",
        headers=Headers({"content-type": declared_mime}),
    )


@pytest.mark.parametrize(
    ("image_format", "expected_mime", "declared_mime"),
    [
        ("JPEG", "image/jpeg", "image/png"),
        ("PNG", "image/png", "image/webp"),
        ("WEBP", "image/webp", "application/octet-stream"),
    ],
)
async def test_upload_uses_detected_mime_type(
    image_format: str,
    expected_mime: str,
    declared_mime: str,
) -> None:
    """Uploads use the signature MIME even when the client label is wrong."""
    service = ImageProcessingService()

    result = await service.process_upload_image(
        _upload(_image_bytes(image_format), declared_mime),
    )

    assert result.mime_type == expected_mime
    assert result.original_format == image_format


def test_base64_uses_detected_mime_type() -> None:
    """Data URI labels do not override the MIME detected from image bytes."""
    service = ImageProcessingService()
    encoded = b64encode(_image_bytes("PNG")).decode("ascii")

    result = service.process_base64(f"data:image/jpeg;base64,{encoded}")

    assert result.mime_type == "image/png"
    assert result.original_format == "PNG"


@pytest.mark.parametrize(
    "content",
    [
        _image_bytes("GIF"),
        b"%PDF-1.4\n%%EOF",
        b"not a recognized file",
    ],
)
async def test_upload_rejects_unsupported_or_unknown_types(content: bytes) -> None:
    """A supported client label cannot disguise invalid image content."""
    service = ImageProcessingService()

    with pytest.raises(ImageProcessingError):
        await service.process_upload_image(_upload(content, "image/png"))


def test_base64_rejects_unsupported_image_type() -> None:
    """Base64 ingestion rejects valid images outside the supported allowlist."""
    service = ImageProcessingService()
    encoded = b64encode(_image_bytes("GIF")).decode("ascii")

    with pytest.raises(ImageProcessingError, match="Unsupported image file type: image/gif"):
        service.process_base64(f"data:image/png;base64,{encoded}")


def test_rejects_signature_and_decoded_format_disagreement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The puremagic and Pillow format results must agree."""
    service = ImageProcessingService()
    encoded = b64encode(_image_bytes("PNG")).decode("ascii")
    monkeypatch.setattr(
        "shared.services.imageprocessing_service.service.detect_image_type",
        lambda content, *, supported_formats: ("image/jpeg", "JPEG"),
    )

    with pytest.raises(ImageProcessingError, match="does not match decoded format PNG"):
        service.process_base64(encoded)
