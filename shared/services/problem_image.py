#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Problem illustration images, shared by the Arena and Contest problem domains.

Both domains store the image in the database as base64 text plus its MIME type and
an optional caption, and both round-trip it through the problem package ZIP as a
root-level ``image.<ext>`` member declared by the ``image`` key of ``problem.json``.
This module owns the size and dimension caps, the extension/MIME maps, the upload
processor, and the staged-image loader so the two domains cannot drift apart.
Deciding *which* archive member is the image belongs to the shared package
reader; what is left here is deciding whether its bytes are an acceptable image.
"""

from __future__ import annotations

from base64 import b64encode
from typing import TYPE_CHECKING

from fastapi import UploadFile

from shared.services.imageprocessing_service import ImageProcessingError, ImageProcessingService

if TYPE_CHECKING:
    # Type-only: the package reader imports this module for its MIME maps, so a
    # runtime import here would close the cycle.
    from shared.services.problem_package.model import PackageImage

MAX_PROBLEM_IMAGE_BYTES = 2 * 1024 * 1024
"""Per-problem image file-size limit (2 MiB)."""

MAX_PROBLEM_IMAGE_WIDTH = 2048
"""Maximum problem-image width in pixels."""

MAX_PROBLEM_IMAGE_HEIGHT = 2048
"""Maximum problem-image height in pixels."""

_MAX_PROBLEM_IMAGE_DIMENSIONS = (MAX_PROBLEM_IMAGE_WIDTH, MAX_PROBLEM_IMAGE_HEIGHT)
_PROBLEM_IMAGE_FORMATS = {*ImageProcessingService.SUPPORTED_FORMATS, "GIF"}

MIME_TO_EXT = {
    "image/gif": "gif",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}
EXT_TO_MIME = {
    "gif": "image/gif",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}


async def process_problem_image_upload(image_service: ImageProcessingService, upload: UploadFile) -> tuple[str, str]:
    """Validate an uploaded problem image and return it as ``(base64, mime)``.

    Args:
        image_service: The application's image processing service.
        upload: The uploaded file, as received from the problem form.

    Returns:
        tuple[str, str]: The base64-encoded image and its MIME type.

    Raises:
        ImageProcessingError: The file is not a supported image.
        ValueError: The file is empty or exceeds a problem-image limit.
    """
    result = await image_service.process_upload_image(
        upload,
        max_file_size=MAX_PROBLEM_IMAGE_BYTES,
        max_dimensions=_MAX_PROBLEM_IMAGE_DIMENSIONS,
        supported_formats=_PROBLEM_IMAGE_FORMATS,
    )
    return result.imagem_base64, result.mime_type


def export_image_filename(mime: str | None) -> str:
    """Return the package filename for an image of the given MIME type."""
    return f"image.{MIME_TO_EXT.get(mime or '', 'png')}"


def load_staged_image(
    image: PackageImage | None,
    image_service: ImageProcessingService,
) -> tuple[str | None, str | None]:
    """Validate a staged package image and return it as ``(base64, mime)``.

    The shared package reader has already resolved which archive member the image
    is and streamed it to disk; what is left is deciding whether the bytes are an
    image this platform accepts, which is what the image service owns.

    Args:
        image: The package's image record, or ``None`` when it ships none.
        image_service: The application's image processing service.

    Returns:
        tuple[str | None, str | None]: The base64 image and its MIME type, or
        ``(None, None)`` when there is no image.

    Raises:
        ValueError: The image is not valid or exceeds a problem-image limit.
    """
    if image is None or image.path is None:
        return None, None
    data_uri = f"data:{image.mime};base64,{b64encode(image.path.read_bytes()).decode('ascii')}"
    try:
        result = image_service.process_base64(
            data_uri,
            max_file_size=MAX_PROBLEM_IMAGE_BYTES,
            max_dimensions=_MAX_PROBLEM_IMAGE_DIMENSIONS,
            supported_formats=_PROBLEM_IMAGE_FORMATS,
        )
    except (ImageProcessingError, ValueError) as exc:
        raise ValueError(f"Invalid problem image: {exc}") from exc
    return result.imagem_base64, result.mime_type
