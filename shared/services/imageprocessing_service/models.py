#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared models for image processing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class ImageProcessingError(Exception):
    """Custom exception for image processing errors."""


@dataclass(frozen=True)
class ImageProcessingConfig:
    """Runtime settings required by ``ImageProcessingService``."""

    avatar_size: int = 256
    max_file_size: int = 5 * 1024 * 1024
    max_width: int = 4096
    max_height: int = 4096
    response_cache_max_age: int = 3600
    font_dir: Path | str | None = None


@dataclass
class ImageBasicMetadata:
    """Basic metadata returned by image validation."""

    mime_type: str
    width: int
    height: int
    size: int
    valid: bool


@dataclass
class ImageProcessingResult:
    """Result of processing an uploaded or base64-encoded image."""

    imagem_base64: str
    avatar_base64: str
    mime_type: str
    original_format: str
    original_dimensions: tuple[int, int]
    avatar_dimensions: tuple[int, int]
    filesize: int
