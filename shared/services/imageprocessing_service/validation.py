#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Validation and conversion helpers for image processing."""

from __future__ import annotations

import io

import puremagic
from PIL import Image, UnidentifiedImageError

from .models import ImageBasicMetadata, ImageProcessingError

MIME_TO_IMAGE_FORMAT = {
    "image/gif": "GIF",
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}


def detect_image_type(content: bytes, *, supported_formats: set[str]) -> tuple[str, str]:
    """Detect and validate an image type from its file signature.

    Args:
        content: Raw image bytes.
        supported_formats: Pillow format names accepted by the service.

    Returns:
        tuple[str, str]: The canonical MIME type and Pillow format name.

    Raises:
        ImageProcessingError: The signature is unknown or its type is unsupported.
    """
    if not content:
        raise ImageProcessingError("Empty or missing image content")

    try:
        detected_mime = puremagic.from_string(content, mime=True)
    except (puremagic.PureError, ValueError) as exc:
        raise ImageProcessingError("File type could not be identified from its content") from exc

    detected_format = MIME_TO_IMAGE_FORMAT.get(detected_mime)
    if detected_format is None or detected_format not in supported_formats:
        allowed_mime_types = []
        for mime_type, image_format in MIME_TO_IMAGE_FORMAT.items():
            if image_format in supported_formats:
                allowed_mime_types.append(mime_type)
        allowed_types = ", ".join(allowed_mime_types)
        unsupported_type = f"Unsupported image file type: {detected_mime}"
        raise ImageProcessingError(f"{unsupported_type}. Allowed: {allowed_types}")

    return detected_mime, detected_format


def image_validation(
    content: bytes | None = None,
    max_image_pixels: int | None = 13000 * 13000,
    max_dimension: int | None = 10000,
    enforce_format: bool = False,
    *,
    supported_formats: set[str],
) -> ImageBasicMetadata:
    """Validate raw image bytes and return basic metadata."""
    if not content:
        raise ImageProcessingError("Empty or missing image content")

    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = max_image_pixels
    try:
        image = Image.open(io.BytesIO(content))
        if enforce_format and image.format not in supported_formats:
            unsupported_format = f"Unsupported image format: {image.format}"
            raise ImageProcessingError(f"{unsupported_format}. Allowed: {supported_formats}")
        image.load()

        width, height = image.size
        if width <= 0 or height <= 0:
            raise ImageProcessingError(f"Invalid image dimensions: {width}x{height}")
        if max_dimension is not None and (width > max_dimension or height > max_dimension):
            raise ImageProcessingError(f"Image excessively large: {width}x{height}")

        format_to_mime = {
            "GIF": "image/gif",
            "PNG": "image/png",
            "JPEG": "image/jpeg",
            "WEBP": "image/webp",
        }
        detected_format = image.format or "PNG"
        return ImageBasicMetadata(
            mime_type=format_to_mime.get(detected_format, "image/png"),
            width=width,
            height=height,
            size=len(content),
            valid=True,
        )
    except UnidentifiedImageError as exc:
        invalid_image = f"File is not a valid image or format not recognized: {exc}"
        raise ImageProcessingError(invalid_image) from exc
    except Image.DecompressionBombError as exc:
        security_error = "Image rejected for security reasons"
        bomb_detail = f"possible decompression bomb: {exc}"
        raise ImageProcessingError(f"{security_error} ({bomb_detail})") from exc
    except OSError as exc:
        raise ImageProcessingError(f"Corrupted or incomplete image: {exc}") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit


def convert_to(content: bytes, output_format: str = "PNG", *, supported_formats: set[str]) -> bytes:
    """Convert raw image bytes to another supported output format."""
    if not content:
        raise ImageProcessingError("Empty or missing image content")

    target_format = output_format.upper().strip()
    if target_format not in supported_formats:
        unsupported_output = f"Unsupported output format: {output_format}"
        raise ImageProcessingError(f"{unsupported_output}. Allowed: {supported_formats}")

    try:
        with Image.open(io.BytesIO(content)) as loaded_image:
            image: Image.Image = loaded_image
            if target_format == "JPEG" and (
                image.mode in ("RGBA", "LA")
                or (image.mode == "P" and "transparency" in image.info)
                or image.mode != "RGB"
            ):
                image = image.convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format=target_format, optimize=True)
            return buffer.getvalue()
    except Exception as exc:
        raise ImageProcessingError(f"Error converting image to {output_format}: {exc}") from exc
