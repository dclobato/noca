#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the portable problem-image limits."""

from __future__ import annotations

import io
import zipfile
from base64 import b64decode

import pytest
from fastapi import UploadFile
from PIL import Image, ImageSequence

from shared.services.imageprocessing_service import ImageProcessingConfig, ImageProcessingService
from shared.services.problem_image import export_image_filename, load_packaged_image, process_problem_image_upload


def _png_bytes(*, width: int, height: int) -> bytes:
    """Return a valid PNG with the requested dimensions."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (255, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


def _animated_gif_bytes() -> bytes:
    """Return a two-frame GIF."""
    buffer = io.BytesIO()
    first_frame = Image.new("RGB", (8, 8), (255, 0, 0))
    second_frame = Image.new("RGB", (8, 8), (0, 0, 255))
    first_frame.save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=[second_frame],
        duration=[100, 200],
        loop=0,
    )
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_upload_uses_fixed_dimensions_instead_of_service_config() -> None:
    """Problem uploads must use the package contract, not deployment settings."""
    image_service = ImageProcessingService(
        ImageProcessingConfig(max_width=1, max_height=1),
    )
    upload = UploadFile(filename="image.png", file=io.BytesIO(_png_bytes(width=2, height=2)))

    image_base64, mime_type = await process_problem_image_upload(image_service, upload)

    assert image_base64
    assert mime_type == "image/png"


@pytest.mark.asyncio
async def test_upload_rejects_image_wider_than_fixed_limit() -> None:
    """A permissive deployment setting must not expand the package contract."""
    image_service = ImageProcessingService(
        ImageProcessingConfig(max_width=4096, max_height=4096),
    )
    upload = UploadFile(filename="image.png", file=io.BytesIO(_png_bytes(width=2049, height=1)))

    with pytest.raises(ValueError, match="Maximum: 2048x2048 pixels"):
        await process_problem_image_upload(image_service, upload)


@pytest.mark.asyncio
async def test_upload_accepts_animated_gif() -> None:
    """Problem uploads accept GIF without enabling it for other image uses."""
    upload = UploadFile(filename="image.gif", file=io.BytesIO(_animated_gif_bytes()))

    image_base64, mime_type = await process_problem_image_upload(ImageProcessingService(), upload)

    assert mime_type == "image/gif"
    with Image.open(io.BytesIO(b64decode(image_base64))) as stored_image:
        assert stored_image.format == "GIF"
        assert stored_image.n_frames == 2
        assert [frame.info["duration"] for frame in ImageSequence.Iterator(stored_image)] == [100, 200]


def test_packaged_image_uses_fixed_dimensions_instead_of_service_config() -> None:
    """Package imports must use the same fixed limits as manual uploads."""
    image_service = ImageProcessingService(
        ImageProcessingConfig(max_width=1, max_height=1),
    )
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("image.png", _png_bytes(width=2, height=2))
    archive_buffer.seek(0)

    with zipfile.ZipFile(archive_buffer) as archive:
        image_base64, mime_type = load_packaged_image(
            {"image": "image.png"},
            archive,
            archive.namelist(),
            image_service,
        )

    assert image_base64
    assert mime_type == "image/png"


def test_packaged_image_rejects_dimensions_above_fixed_limit() -> None:
    """Package imports must reject images outside the portable contract."""
    image_service = ImageProcessingService(
        ImageProcessingConfig(max_width=4096, max_height=4096),
    )
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("image.png", _png_bytes(width=1, height=2049))
    archive_buffer.seek(0)

    with (
        zipfile.ZipFile(archive_buffer) as archive,
        pytest.raises(ValueError, match="Maximum: 2048x2048 pixels"),
    ):
        load_packaged_image(
            {"image": "image.png"},
            archive,
            archive.namelist(),
            image_service,
        )


def test_packaged_gif_round_trips_with_gif_extension() -> None:
    """GIF package imports retain their MIME type and export extension."""
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("image.gif", _animated_gif_bytes())
    archive_buffer.seek(0)

    with zipfile.ZipFile(archive_buffer) as archive:
        image_base64, mime_type = load_packaged_image(
            {"image": "image.gif"},
            archive,
            archive.namelist(),
            ImageProcessingService(),
        )

    assert image_base64
    assert mime_type == "image/gif"
    assert export_image_filename(mime_type) == "image.gif"
