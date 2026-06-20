#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Compatibility re-exports for shared network utility errors."""

from shared.services.network_utils.errors import (
    HeadersValidationError,
    NetworkServiceError,
    ParamsValidationError,
    RequestValidationError,
    SSRFProtectionError,
    URLValidationError,
)

__all__ = [
    "HeadersValidationError",
    "NetworkServiceError",
    "ParamsValidationError",
    "RequestValidationError",
    "SSRFProtectionError",
    "URLValidationError",
]
