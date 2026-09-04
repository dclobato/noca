#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Credential accessors for the Arena user model.

Holds the email, password, TOTP-secret and AI-API-key getter/setter pairs plus
the JWT token identity, all of which wrap a stored column with normalization,
hashing, or encryption. They are grouped here because they share one property:
assigning to them has side effects beyond the column being written.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Mapped


def _utcnow() -> datetime:
    """Return current UTC datetime.

    Returns:
        datetime: Current UTC time as timezone-aware datetime.
    """
    return datetime.now(UTC)


class ArenaUserCredentialsMixin:
    """Provide credential accessors for the Arena user model."""

    id: Mapped[str]
    email_normalizado: Mapped[str]
    email_canonical: Mapped[str | None]
    password_hash: Mapped[str]
    password_is_placeholder: Mapped[bool]
    dta_ultima_alteracao_senha: Mapped[datetime | None]
    precisa_trocar_senha: Mapped[bool]
    dta_marcacao_troca_senha: Mapped[datetime | None]
    session_version: Mapped[int]
    _otp_secret: Mapped[str | None]
    _ai_api_key: Mapped[str | None]

    # ------------------------------------------------------------------
    # email getter/setter
    # ------------------------------------------------------------------

    @property
    def email(self) -> str:
        """Return the normalised email address.

        Returns:
            str: Normalised email.
        """
        return self.email_normalizado

    @email.setter
    def email(self, value: str) -> None:
        """Normalise and store the email address.

        Args:
            value: Raw email address to normalise.

        Raises:
            ValueError: If the email address is invalid.
        """
        from shared.services.email_validation import EmailValidationService

        try:
            self.email_normalizado = EmailValidationService.normalize(value)
            self.email_canonical = EmailValidationService.canonicalize(value)
        except ValueError as exc:
            raise ValueError(f"Invalid email address: {value}") from exc

    # ------------------------------------------------------------------
    # email_mascarado
    # ------------------------------------------------------------------

    @property
    def email_mascarado(self) -> str:
        """Return a masked email suitable for display (e.g. use***@ex****.com).

        Returns:
            str: Masked email address.
        """
        from shared.services.email_validation import EmailValidationService

        return EmailValidationService.mask(self.email_normalizado)

    # ------------------------------------------------------------------
    # password getter/setter
    # ------------------------------------------------------------------

    @property
    def password(self) -> str:
        """Return the stored password hash.

        Returns:
            str: Werkzeug password hash.
        """
        return self.password_hash

    @password.setter
    def password(self, value: str) -> None:
        """Hash and store a new password, updating related security fields.

        Sets dta_ultima_alteracao_senha, clears precisa_trocar_senha, and
        increments session_version to invalidate all existing JWT tokens.

        This is the only path in the codebase that writes password_hash, which is
        why clearing password_is_placeholder belongs here: an account created by a
        Google-first signup becomes password-capable the moment a real password is
        set, whether through the reset flow or the change-password form, with no
        caller obliged to remember to clear the flag.

        Args:
            value: Plaintext password.
        """
        from werkzeug.security import generate_password_hash

        self.password_hash = generate_password_hash(value)
        self.password_is_placeholder = False
        self.dta_ultima_alteracao_senha = _utcnow()
        self.precisa_trocar_senha = False
        self.dta_marcacao_troca_senha = None
        current = self.session_version if self.session_version is not None else 0
        self.session_version = (current + 1) % 65536

    def check_password(self, password: str) -> bool:
        """Verify a plaintext password against the stored hash.

        Args:
            password: Plaintext password to verify.

        Returns:
            bool: True if the password matches.
        """
        from werkzeug.security import check_password_hash

        return check_password_hash(str(self.password_hash), password)

    @property
    def has_usable_password(self) -> bool:
        """Whether this account can be logged into with a password.

        False only for an account created by a Google-first signup that has not
        since set a password. Such an account holds a real Werkzeug hash of a
        random secret -- so get_token_id() and session_version behave exactly as
        for any other account -- but no password can ever match it.

        Returns:
            bool: True when password login is possible for this account.
        """
        return not self.password_is_placeholder

    # ------------------------------------------------------------------
    # otp_secret getter/setter
    # ------------------------------------------------------------------

    @property
    def otp_secret(self) -> str | None:
        """Return the decrypted OTP secret.

        Returns:
            str | None: Decrypted TOTP secret, or None if not set.
        """
        return self._otp_secret

    @otp_secret.setter
    def otp_secret(self, value: str | None) -> None:
        """Store the OTP secret (encrypted at rest by EncryptedString).

        Args:
            value: Plaintext TOTP secret, or None to clear.
        """
        self._otp_secret = value

    # ------------------------------------------------------------------
    # ai_api_key getter/setter
    # ------------------------------------------------------------------

    @property
    def ai_api_key(self) -> str | None:
        """Return the decrypted AI provider API key.

        Returns:
            str | None: Decrypted API key, or None if not set.
        """
        return self._ai_api_key

    @ai_api_key.setter
    def ai_api_key(self, value: str | None) -> None:
        """Store the AI provider API key (encrypted at rest by EncryptedString).

        Args:
            value: Plaintext API key, or None to clear.
        """
        self._ai_api_key = value

    # ------------------------------------------------------------------
    # token identity
    # ------------------------------------------------------------------

    def get_token_id(self) -> str:
        """Return a compound token identity string for JWT subject claims.

        Combines the user ID, a suffix of the password hash, and the current
        session_version. Any JWT carrying an older session_version (after a
        password change) is automatically invalid.

        Returns:
            str: "{id}|{last_15_chars_of_hash}|{session_version}"
        """
        return f"{self.id}|{self.password_hash[-15:]}|{self.session_version}"
