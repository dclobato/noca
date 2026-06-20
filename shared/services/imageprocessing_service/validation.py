#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Validation and conversion helpers for image processing."""

from __future__ import annotations

import io

from PIL import Image, UnidentifiedImageError

from .models import ImageBasicMetadata, ImageProcessingError


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
            raise ImageProcessingError(f"Unsupported image format: {image.format}. Allowed: {supported_formats}")
        image.load()

        width, height = image.size
        if width <= 0 or height <= 0:
            raise ImageProcessingError(f"Invalid image dimensions: {width}x{height}")
        if max_dimension is not None and (width > max_dimension or height > max_dimension):
            raise ImageProcessingError(f"Image excessively large: {width}x{height}")

        format_to_mime = {
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
        raise ImageProcessingError(f"File is not a valid image or format not recognized: {exc}") from exc
    except Image.DecompressionBombError as exc:
        raise ImageProcessingError(f"Image rejected for security reasons (possible decompression bomb): {exc}") from exc
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
        raise ImageProcessingError(f"Unsupported output format: {output_format}. Allowed: {supported_formats}")

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
