#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Compatibility wrappers for the shared password service."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from werkzeug.security import check_password_hash

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


class PasswordHashActor(Protocol):
    """Actor exposing a Werkzeug-compatible password hash."""

    password_hash: str


def password_matches(actor: PasswordHashActor, password: str) -> bool:
    """Return whether a non-empty password matches an actor's stored hash."""
    return bool(password) and check_password_hash(actor.password_hash, password)


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
    "password_matches",
]
