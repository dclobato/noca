#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena JWT action enum and re-exports from the jwtservice package.

All arena service modules should import JWT utilities from this module
so that the token contract is defined in one place.

The ``ArenaTokenAction`` enum mirrors ``arena.main.ArenaTokenAction``; both
must stay in sync.  The ``JWTService`` instance stored in ``app.state``
must be initialised with ``action_enum=ArenaTokenAction`` so that token
action claims are resolved correctly during validation.
"""

from enum import StrEnum

from jwtservice import (  # noqa: F401 — deliberately re-exported
    JWTService,
    JWTServiceError,
    TokenConfig,
    TokenCreationError,
    TokenValidationError,
    TokenVerificationResult,
    load_token_config_from_dict,
)

__all__ = [
    "ARENA_JWT_ISSUER",
    "ArenaTokenAction",
    "JWTService",
    "JWTServiceError",
    "TokenConfig",
    "TokenCreationError",
    "TokenValidationError",
    "TokenVerificationResult",
    "load_token_config_from_dict",
]

_TWO_FA_SESSION_TIMEOUT = 90
_PASSWORD_CHANGE_TIMEOUT = 300
_EMAIL_VALIDATION_TIMEOUT = 86_400  # 24 hours
_RESET_PASSWORD_TIMEOUT = 3_600  # 1 hour
ARENA_JWT_ISSUER = "noca-arena"


class ArenaTokenAction(StrEnum):
    """JWT action claims for all Arena token flows.

    Attributes:
        LOGIN: Standard authenticated session token.
        VALIDATE_EMAIL: Short-lived token for email address confirmation.
        PARENTAL_CONSENT: Short-lived token for parent/legal guardian consent.
        RESET_PASSWORD: Short-lived token for password reset via email link.
        PENDING_2FA: Intermediate token issued after password check while
            waiting for the user to supply their TOTP or backup code.
        ACTIVATING_2FA: Intermediate token issued when the user starts the
            2FA activation flow; the TOTP secret is stored in the DB, not
            in this token.
        PENDING_PASSWORD_CHANGE: Intermediate token issued when a forced
            password change is required before a full session is granted.
    """

    LOGIN = "login"
    VALIDATE_EMAIL = "validate_email"
    PARENTAL_CONSENT = "parental_consent"
    RESET_PASSWORD = "reset_password"
    PENDING_2FA = "pending_2fa"
    ACTIVATING_2FA = "activating_2fa"
    PENDING_PASSWORD_CHANGE = "pending_password_change"
