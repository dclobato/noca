#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Compatibility re-exports for shared image processing helpers."""

from shared.services.imageprocessing_service.helpers import (
    build_placeholder,
    calculate_max_font_size,
    crop_to_aspect_ratio,
    generate_avatar,
    resolve_font_path,
)

__all__ = [
    "build_placeholder",
    "calculate_max_font_size",
    "crop_to_aspect_ratio",
    "generate_avatar",
    "resolve_font_path",
]
