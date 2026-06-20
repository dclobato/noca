#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Compatibility wrappers for the shared password service."""

from __future__ import annotations

from pathlib import Path

from shared.services.password_service import (
    PasswordPolicy as SharedPasswordPolicy,
)
from shared.services.password_service import (
    PasswordPolicyError,
    PasswordSettings,
)
from shared.services.password_service import (
    generate_diceware_password as shared_generate_diceware_password,
)
from web.config import settings


class PasswordPolicy(SharedPasswordPolicy):
    """Web-configured password policy."""

    def __init__(self) -> None:
        """Initialize the policy with web settings."""
        super().__init__(settings)


def generate_diceware_password(*, wordlist_path: Path | None = None, size: int | None = None) -> str:
    """Generate a diceware-style password using web settings.

    Args:
        wordlist_path: Optional explicit wordlist path.
        size: Optional number of initial diceware words to include.

    Returns:
        A shuffled passphrase joined with `-`.
    """
    return shared_generate_diceware_password(settings, wordlist_path=wordlist_path, size=size)


__all__ = [
    "PasswordPolicy",
    "PasswordPolicyError",
    "PasswordSettings",
    "generate_diceware_password",
]
