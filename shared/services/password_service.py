#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared password generation helpers and policy validation."""

from __future__ import annotations

import re
import secrets
from functools import lru_cache
from pathlib import Path
from random import sample
from typing import Protocol


class PasswordSettings(Protocol):
    """Settings contract required by password helpers."""

    WORDLIST_FILENAME: str
    PASSWORD_WORD_COUNT: int
    MIN_PASSWORD_LENGTH: int
    PASSWORD_UPPERCASE_REQUIRED: bool
    PASSWORD_LOWERCASE_REQUIRED: bool
    PASSWORD_NUMBER_REQUIRED: bool
    PASSWORD_SYMBOL_REQUIRED: bool


class PasswordPolicyError(ValueError):
    """Raised when a password does not satisfy strength requirements."""


def _default_wordlist_path(settings: PasswordSettings) -> Path:
    """Resolve the default diceware wordlist path from application settings.

    Args:
        settings: Runtime settings containing the configured wordlist filename.

    Returns:
        The absolute path to the configured wordlist file.
    """
    return Path(__file__).resolve().parents[1] / settings.WORDLIST_FILENAME


@lru_cache(maxsize=4)
def _load_wordlist(path: Path) -> dict[str, str]:
    """Load a diceware wordlist file into memory.

    Args:
        path: Absolute path to the wordlist file.

    Returns:
        A mapping from dice codes to words.
    """
    mapping: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        code, word = line.split("\t", 1)
        mapping[code] = word
    return mapping


def _pick_word(mapping: dict[str, str]) -> str:
    """Pick a random diceware word from a loaded wordlist mapping.

    Args:
        mapping: Wordlist mapping keyed by five-digit dice codes.

    Returns:
        A randomly selected word from the mapping.
    """
    key = "".join(str(secrets.randbelow(6) + 1) for _ in range(5))
    return mapping[key]


def generate_diceware_password(
    settings: PasswordSettings,
    *,
    wordlist_path: Path | None = None,
    size: int | None = None,
) -> str:
    """Generate a diceware-style password aligned with the configured policy.

    Args:
        settings: Runtime settings containing password policy values.
        wordlist_path: Optional explicit wordlist path. When omitted, the path
            configured in application settings is used.
        size: Optional number of initial diceware words to include. When omitted,
            the configured default word count is used.

    Returns:
        A shuffled passphrase joined with `-`.

    Notes:
        The generated password is adapted to the configured password policy:
        uppercase can be satisfied by capitalizing the first word, numbers by
        appending a random digit, symbols by the `-` separator, and minimum
        length by appending extra words until the requirement is met.
    """
    mapping = _load_wordlist(wordlist_path or _default_wordlist_path(settings))
    word_count = size if size is not None else settings.PASSWORD_WORD_COUNT

    words: list[str] = [_pick_word(mapping) for _ in range(word_count)]
    if settings.PASSWORD_NUMBER_REQUIRED:
        words.append(str(secrets.randbelow(10)))

    # The separator "-" contributes (n_parts - 1) characters to the total.
    while len("-".join(words)) < settings.MIN_PASSWORD_LENGTH:
        words.append(_pick_word(mapping))

    if not any(ch.isalpha() for part in words for ch in part):
        words.append(_pick_word(mapping))

    if settings.PASSWORD_UPPERCASE_REQUIRED and not any(ch.isupper() for part in words for ch in part):
        for i, part in enumerate(words):
            if any(ch.isalpha() for ch in part):
                words[i] = part.capitalize()
                break

    if settings.PASSWORD_LOWERCASE_REQUIRED and not any(ch.islower() for part in words for ch in part):
        words.append(_pick_word(mapping).lower())

    return "-".join(sample(words, k=len(words)))


class PasswordPolicy:
    """Validate passwords against configured complexity requirements."""

    _UPPERCASE_RE = re.compile(r"[A-Z]")
    _LOWERCASE_RE = re.compile(r"[a-z]")
    _NUMBER_RE = re.compile(r"\d")
    _SYMBOL_RE = re.compile(r"\W")

    def __init__(self, settings: PasswordSettings) -> None:
        """Initialize a password policy.

        Args:
            settings: Runtime settings containing password policy values.
        """
        self._settings = settings

    @property
    def policy_hint(self) -> str:
        """Return the password requirements hint for display in UI forms.

        Returns:
            A user-facing string describing the current password policy.
        """
        return self._error_message

    @property
    def _error_message(self) -> str:
        """Build the current policy error message from configured requirements.

        Returns:
            A user-facing validation error message.
        """
        requirements: list[str] = []
        if self._settings.PASSWORD_UPPERCASE_REQUIRED:
            requirements.append("uppercase")
        if self._settings.PASSWORD_LOWERCASE_REQUIRED:
            requirements.append("lowercase")
        if self._settings.PASSWORD_NUMBER_REQUIRED:
            requirements.append("number")
        if self._settings.PASSWORD_SYMBOL_REQUIRED:
            requirements.append("symbol")

        if requirements:
            return (
                f"Password must be at least {self._settings.MIN_PASSWORD_LENGTH} "
                f"characters and include {', '.join(requirements)}."
            )
        return f"Password must be at least {self._settings.MIN_PASSWORD_LENGTH} characters."

    def validate_new_password(self, password: str) -> None:
        """Validate a plaintext password against the configured policy.

        Args:
            password: Plaintext password to validate.

        Returns:
            None.

        Raises:
            PasswordPolicyError: If the password violates the configured minimum
                length or any enabled uppercase, lowercase, number, or symbol requirement.
        """
        if len(password) < self._settings.MIN_PASSWORD_LENGTH:
            raise PasswordPolicyError(self._error_message)
        if self._settings.PASSWORD_UPPERCASE_REQUIRED and self._UPPERCASE_RE.search(password) is None:
            raise PasswordPolicyError(self._error_message)
        if self._settings.PASSWORD_LOWERCASE_REQUIRED and self._LOWERCASE_RE.search(password) is None:
            raise PasswordPolicyError(self._error_message)
        if self._settings.PASSWORD_NUMBER_REQUIRED and self._NUMBER_RE.search(password) is None:
            raise PasswordPolicyError(self._error_message)
        if self._settings.PASSWORD_SYMBOL_REQUIRED and self._SYMBOL_RE.search(password) is None:
            raise PasswordPolicyError(self._error_message)
