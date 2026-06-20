#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Helper functions for image processing."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .models import ImageProcessingError


def crop_to_aspect_ratio(
    image: Image.Image,
    aspect_width: int = 2,
    aspect_height: int = 3,
) -> Image.Image:
    """Crop an image to a centered target aspect ratio."""
    original_width, original_height = image.size
    required_aspect_ratio = aspect_width / aspect_height
    current_aspect_ratio = original_width / original_height

    if current_aspect_ratio > required_aspect_ratio:
        new_width = int(original_height * required_aspect_ratio)
        left = (original_width - new_width) // 2
        return image.crop((left, 0, left + new_width, original_height))

    new_height = int(original_width / required_aspect_ratio)
    top = (original_height - new_height) // 2
    return image.crop((0, top, original_width, top + new_height))


def resolve_font_path(font_file: str, font_dir: Path | str | None = None) -> Path | None:
    """Resolve a configured font file path for placeholder generation."""
    if not font_dir:
        return None
    font_path = Path(font_dir) / font_file
    return font_path if font_path.exists() else None


def calculate_max_font_size(
    text: str,
    image_size: tuple[int, int],
    font_path: Path | None = None,
    margin: int = 0,
) -> int:
    """Calculate the largest font size that fits within a target image area."""
    img_width, img_height = image_size
    max_width = img_width - 2 * margin
    max_height = img_height - 2 * margin
    draw = ImageDraw.Draw(Image.new("RGB", image_size))

    use_truetype = False
    if font_path:
        try:
            ImageFont.truetype(str(font_path), 10)
            use_truetype = True
        except OSError, ValueError:
            use_truetype = False

    low, high = 1, 500
    best_size = low
    while low <= high:
        mid = (low + high) // 2
        font: Any
        if use_truetype:
            try:
                font = ImageFont.truetype(str(font_path), mid)
            except OSError, ValueError:
                font = ImageFont.load_default(size=mid)
        else:
            font = ImageFont.load_default(size=mid)

        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        if text_width <= max_width and text_height <= max_height:
            best_size = mid
            low = mid + 1
        else:
            high = mid - 1
    return best_size


def generate_avatar(image: Image.Image, avatar_size: int) -> tuple[bytes, tuple[int, int]]:
    """Generate a resized avatar image from a Pillow image."""
    width, height = image.size
    original_format = image.format
    avatar_image = image.copy()
    if max(width, height) > avatar_size:
        scale_factor = min(avatar_size / width, avatar_size / height)
        new_dimensions = (int(width * scale_factor), int(height * scale_factor))
        avatar_image.thumbnail(new_dimensions, Image.Resampling.LANCZOS)

    buffer_avatar = io.BytesIO()
    avatar_image.save(buffer_avatar, format=original_format, optimize=True)
    return buffer_avatar.getvalue(), avatar_image.size


def build_placeholder(
    width: int,
    height: int,
    text: str | None = None,
    fontsize: int | None = None,
    font_file: str = "arial.ttf",
    bg_color: str = "#6c757d",
    font_dir: Path | str | None = None,
) -> bytes:
    """Generate a PNG placeholder image with optional centered text."""
    if text and fontsize is None:
        raise ImageProcessingError("If 'text' is provided, 'fontsize' must be specified.")

    image = Image.new("RGB", (width, height), color=bg_color)
    draw = ImageDraw.Draw(image)

    if text and fontsize is not None:
        font_path = resolve_font_path(font_file, font_dir=font_dir)
        if fontsize == -1:
            fontsize = calculate_max_font_size(text=text, image_size=(width, height), font_path=font_path, margin=20)
        try:
            font: Any
            if font_path is not None:
                font = ImageFont.truetype(str(font_path), fontsize)
            else:
                font = ImageFont.load_default(size=fontsize)
        except OSError, ValueError:
            font = ImageFont.load_default(size=fontsize)
        except Exception as exc:
            raise ImageProcessingError(f"Error loading font: {exc}") from exc

        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        position = ((width - text_width) // 2, (height - text_height) // 2)
        draw.text(position, text, fill="white", align="center", font=font)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer.getvalue()
