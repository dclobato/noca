#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Error types for network utilities."""


class NetworkServiceError(Exception):
    """Base class for NetworkService-related errors."""


class RequestValidationError(NetworkServiceError):
    """Base class for request validation errors."""


class URLValidationError(RequestValidationError):
    """Error during URL validation."""


class SSRFProtectionError(URLValidationError):
    """URL points to a private network or localhost."""


class ParamsValidationError(RequestValidationError):
    """Error during query parameter validation."""


class HeadersValidationError(RequestValidationError):
    """Error during header validation."""
