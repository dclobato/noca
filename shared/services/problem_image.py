#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Problem illustration images, shared by the Arena and Contest problem domains.

Both domains store the image in the database as base64 text plus its MIME type and
an optional caption, and both round-trip it through the problem package ZIP as a
root-level ``image.<ext>`` member declared by the ``image`` key of ``problem.json``.
This module owns the size and dimension caps, the extension/MIME maps, the upload
processor, and the packaged-image loader so the two domains cannot drift apart.
"""

from __future__ import annotations

import zipfile
from base64 import b64encode
from typing import Any

from fastapi import UploadFile

from shared.services.imageprocessing_service import ImageProcessingError, ImageProcessingService

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


def find_packaged_image(names: list[str]) -> str | None:
    """Return the first root-level image file in the archive, if any."""
    for name in names:
        if "/" in name:
            continue
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in EXT_TO_MIME:
            return name
    return None


def load_packaged_image(
    meta: dict[str, Any],
    archive: zipfile.ZipFile,
    names: list[str],
    image_service: ImageProcessingService,
) -> tuple[str | None, str | None]:
    """Validate and return the packaged image as ``(base64, mime)`` or ``(None, None)``.

    When ``problem.json`` explicitly references an image filename, that file must
    exist in the archive — a missing referenced image rejects the package rather
    than silently dropping the image. Only when no image is referenced does the
    loader fall back to auto-detecting a root-level image file.

    Args:
        meta: The parsed ``problem.json`` mapping.
        archive: The open problem package archive.
        names: The archive member names.
        image_service: The application's image processing service.

    Returns:
        tuple[str | None, str | None]: The base64 image and its MIME type, or
        ``(None, None)`` when the package ships no image.

    Raises:
        ValueError: The referenced image is missing, or the image is invalid.
    """
    referenced = meta.get("image")
    filename: str | None
    if isinstance(referenced, str) and referenced.strip():
        filename = referenced.strip()
        if filename not in names:
            raise ValueError(f"problem.json references image '{filename}' which is not present in the ZIP.")
    else:
        filename = find_packaged_image(names)
    if not filename:
        return None, None

    image_bytes = archive.read(filename)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    mime = EXT_TO_MIME.get(ext, "image/png")
    data_uri = f"data:{mime};base64,{b64encode(image_bytes).decode('ascii')}"
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
