#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared email validation and normalization helpers."""

from __future__ import annotations

from typing import Any

from shared.services.email_models import build_rfc5322_address


class EmailValidationService:
    """Utility methods for email validation and normalization."""

    @staticmethod
    def _validated(email: str) -> Any | None:
        """Return validator object when valid; otherwise ``None``."""
        from email_validator import validate_email
        from email_validator.exceptions import EmailNotValidError, EmailSyntaxError

        try:
            return validate_email(email, check_deliverability=False)
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, (EmailNotValidError, EmailSyntaxError, TypeError)):
                return None
            raise

    @staticmethod
    def is_valid(email: str) -> bool:
        """Validate an email address."""
        return EmailValidationService._validated(email) is not None

    @staticmethod
    def normalize(email: str) -> str:
        """Normalize an email address.

        Raises:
            ValueError: If email is invalid.
        """
        validated = EmailValidationService._validated(email)
        if validated is None:
            raise ValueError("Endereco de email invalido.")
        return str(validated.normalized).lower()

    @staticmethod
    def canonicalize(email: str) -> str:
        """Return the canonical/root form used for alias-collision detection.

        Strips ``+tag`` subaddressing and removes all dots from the local part
        (for every domain), on top of :meth:`normalize`'s lowercasing and
        validation, so that mailbox aliases that reach the same inbox collapse
        to a single value (e.g. ``daniel+test@x`` and ``d.a.niel@x`` both map to
        ``daniel@x``).

        Args:
            email: Raw email address to canonicalize.

        Returns:
            str: Canonical/root email address.

        Raises:
            ValueError: If email is invalid.
        """
        normalized = EmailValidationService.normalize(email)
        local, _, domain = normalized.partition("@")
        canonical_local = local.split("+", 1)[0].replace(".", "")
        if not canonical_local:  # e.g. "+tag@x" / ".@x" -- fall back to local
            canonical_local = local
        return f"{canonical_local}@{domain}"

    @staticmethod
    def montar_destinatario(nome: str | None, email: str) -> str:
        """Build an RFC 5322 display-name + address string."""
        return build_rfc5322_address(nome, email)

    @staticmethod
    def mask(email: str) -> str:
        """Return a masked email suitable for public display.

        Reveals only the first few characters of the local part and domain name,
        keeping the TLD intact (e.g. ``use***@ex****.com``). This is the single
        source of truth for how email addresses are partially hidden in
        public-facing surfaces.

        Args:
            email: Normalised email address.

        Returns:
            str: Masked email address.
        """
        show_first = 3
        show_last_domain = 2
        username, domain = email.split("@", 1)
        masked_user = (username[:show_first] if len(username) > show_first else username) + "***"
        domain_parts = domain.split(".")
        if len(domain_parts) > 1:
            domain_name = ".".join(domain_parts[:-1])
            tld = domain_parts[-1]
            masked_domain_name = (
                domain_name[:show_last_domain] if len(domain_name) > show_last_domain else domain_name
            ) + "****"
            masked_domain = f"{masked_domain_name}.{tld}"
        else:
            masked_domain = "****"
        return f"{masked_user}@{masked_domain}"
