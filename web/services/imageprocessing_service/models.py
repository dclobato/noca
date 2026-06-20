#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Compatibility re-exports for shared image processing models."""

from shared.services.imageprocessing_service.models import (
    ImageBasicMetadata,
    ImageProcessingConfig,
    ImageProcessingError,
    ImageProcessingResult,
)

__all__ = ["ImageBasicMetadata", "ImageProcessingConfig", "ImageProcessingError", "ImageProcessingResult"]
