#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared image processing service."""

from __future__ import annotations

import io
import logging
import re
from base64 import b64decode, b64encode

from fastapi import Response, UploadFile
from PIL import Image

from .helpers import build_placeholder, crop_to_aspect_ratio, generate_avatar
from .models import ImageBasicMetadata, ImageProcessingConfig, ImageProcessingError, ImageProcessingResult
from .validation import convert_to as convert_image_to
from .validation import image_validation as validate_image_bytes


class ImageProcessingService:
    """Process uploaded images, avatars, placeholders, and image conversions."""

    SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP"}
    ALLOWED_EXTENSIONS = ["png", "jpg", "jpeg", "webp"]
    JPEG_QUALITY = 85
    PNG_OPTIMIZE = True

    def __init__(
        self,
        config: ImageProcessingConfig | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the image processing service."""
        self._config = config or ImageProcessingConfig()
        self._logger = logger or logging.getLogger(__name__)
        self._logger.debug("ImageProcessingService initialized")

    async def process_upload_image(
        self,
        upload: UploadFile,
        avatar_size: int | None = None,
        max_file_size: int | None = None,
        max_dimensions: tuple[int, int] | None = None,
        crop_aspect_ratio: bool = False,
        aspect_width: int = 2,
        aspect_height: int = 3,
    ) -> ImageProcessingResult:
        """Process an uploaded image and generate a stored image plus avatar."""
        if upload is None:
            raise ValueError("No file provided")

        avatar_size = avatar_size or self._config.avatar_size
        max_file_size = max_file_size or self._config.max_file_size
        max_dimensions = max_dimensions or (self._config.max_width, self._config.max_height)

        try:
            await upload.seek(0)
            image_data = await upload.read()
            if not image_data:
                raise ValueError("Empty image file")
            if len(image_data) > max_file_size:
                raise ValueError(f"File too large. Maximum allowed: {max_file_size / (1024 * 1024):.1f}MB")

            mime_type = upload.content_type or "application/octet-stream"
            return self._process_image_bytes(
                image_data=image_data,
                mime_type=mime_type,
                avatar_size=avatar_size,
                max_dimensions=max_dimensions,
                crop_aspect_ratio=crop_aspect_ratio,
                aspect_width=aspect_width,
                aspect_height=aspect_height,
            )
        except (AttributeError, OSError) as exc:
            raise ImageProcessingError(f"Error processing image file: {exc}") from exc

    def process_base64(
        self,
        base64_string: str,
        avatar_size: int | None = None,
        max_file_size: int | None = None,
        max_dimensions: tuple[int, int] | None = None,
    ) -> ImageProcessingResult:
        """Process a base64-encoded image and generate a stored image plus avatar."""
        if not base64_string:
            raise ValueError("Empty base64 string")

        avatar_size = avatar_size or self._config.avatar_size
        max_file_size = max_file_size or self._config.max_file_size
        max_dimensions = max_dimensions or (self._config.max_width, self._config.max_height)

        try:
            mime_type = "image/jpeg"
            if base64_string.startswith("data:"):
                match = re.match(r"data:(image/[a-z]+);base64,(.+)", base64_string)
                if match:
                    mime_type = match.group(1)
                    base64_string = match.group(2)
                else:
                    raise ValueError("Invalid data URI format")

            image_data = b64decode(base64_string)
            if not image_data:
                raise ValueError("Empty image data after decoding")
            if len(image_data) > max_file_size:
                raise ValueError(f"File too large. Maximum allowed: {max_file_size / (1024 * 1024):.1f}MB")

            return self._process_image_bytes(
                image_data=image_data,
                mime_type=mime_type,
                avatar_size=avatar_size,
                max_dimensions=max_dimensions,
            )
        except (ValueError, TypeError) as exc:
            if isinstance(exc, ValueError):
                raise
            raise ImageProcessingError(f"Error decoding base64: {exc}") from exc

    def _process_image_bytes(
        self,
        image_data: bytes,
        mime_type: str,
        avatar_size: int,
        max_dimensions: tuple[int, int],
        crop_aspect_ratio: bool = False,
        aspect_width: int = 2,
        aspect_height: int = 3,
    ) -> ImageProcessingResult:
        """Process validated image bytes and generate a stored image plus avatar."""
        try:
            with Image.open(io.BytesIO(image_data)) as loaded_image:
                image: Image.Image = loaded_image
                if not hasattr(image, "format") or image.format is None:
                    raise ImageProcessingError("Unrecognized image format")
                if image.format not in self.SUPPORTED_FORMATS:
                    raise ImageProcessingError(
                        f"Format {image.format} not supported. Accepted formats: {', '.join(self.SUPPORTED_FORMATS)}"
                    )

                original_width, original_height = image.size
                if original_width > max_dimensions[0] or original_height > max_dimensions[1]:
                    raise ValueError(f"Image too large. Maximum: {max_dimensions[0]}x{max_dimensions[1]} pixels")

                original_format = image.format
                if crop_aspect_ratio:
                    image = crop_to_aspect_ratio(image, aspect_width, aspect_height)
                    image.format = original_format

                image_buffer = io.BytesIO()
                image.save(image_buffer, format=original_format, optimize=True)
                stored_bytes = image_buffer.getvalue()
                avatar_data, avatar_dims = generate_avatar(image, avatar_size)

                return ImageProcessingResult(
                    imagem_base64=b64encode(stored_bytes).decode("utf-8"),
                    avatar_base64=b64encode(avatar_data).decode("utf-8"),
                    mime_type=mime_type,
                    original_format=original_format,
                    original_dimensions=(original_width, original_height),
                    avatar_dimensions=avatar_dims,
                    filesize=len(image_data),
                )
        except Exception as exc:
            if isinstance(exc, (ImageProcessingError, ValueError)):
                raise
            raise ImageProcessingError(f"Error processing image: {exc}") from exc

    crop_to_aspect_ratio = staticmethod(crop_to_aspect_ratio)

    def generate_placeholder(
        self,
        width: int,
        height: int,
        text: str | None = None,
        fontsize: int | None = None,
        font_file: str = "arial.ttf",
        bg_color: str = "#6c757d",
    ) -> bytes:
        """Generate a PNG placeholder image with optional centered text."""
        return build_placeholder(
            width=width,
            height=height,
            text=text,
            fontsize=fontsize,
            font_file=font_file,
            bg_color=bg_color,
            font_dir=self._config.font_dir,
        )

    def build_image_response(
        self,
        image_data: bytes,
        mime_type: str = "image/png",
        *,
        cache_directive: str = "public",
    ) -> Response:
        """Build a FastAPI response for serving image bytes."""
        max_age = self._config.response_cache_max_age
        return Response(
            content=image_data,
            media_type=mime_type,
            headers={"Cache-Control": f"{cache_directive}, max-age={max_age}"},
        )

    def image_validation(
        self,
        content: bytes | None = None,
        max_image_pixels: int | None = 13000 * 13000,
        max_dimension: int | None = 10000,
        enforce_format: bool = False,
    ) -> ImageBasicMetadata:
        """Validate raw image bytes and return basic metadata."""
        return validate_image_bytes(
            content=content,
            max_image_pixels=max_image_pixels,
            max_dimension=max_dimension,
            enforce_format=enforce_format,
            supported_formats=self.SUPPORTED_FORMATS,
        )

    def convert_to(self, content: bytes, output_format: str = "PNG") -> bytes:
        """Convert raw image bytes to another supported output format."""
        return convert_image_to(content=content, output_format=output_format, supported_formats=self.SUPPORTED_FORMATS)
