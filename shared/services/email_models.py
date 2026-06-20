#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared email payload models and address helpers."""

from dataclasses import dataclass
from typing import Any


def build_rfc5322_address(nome: str | None, email: str) -> str:
    """Build an RFC 5322 display-name + address string.

    Args:
        nome: Optional display name.
        email: Email address (addr-spec).

    Returns:
        Formatted address string, or bare ``email`` if the name is invalid.
    """
    from email.headerregistry import Address

    try:
        return str(Address(display_name=nome or "", addr_spec=email))
    except ValueError, TypeError:
        return email


@dataclass(frozen=True)
class EmailMessage:
    """Email payload used by providers."""

    from_email: str | None = None
    from_name: str | None = None
    to_email: str | None = None
    to_name: str | None = None
    cc_email: str | None = None
    cc_name: str | None = None
    subject: str | None = None
    text_body: str | None = None
    html_body: str | None = None

    def __post_init__(self) -> None:
        """Validate required message fields."""
        if not self.to_email:
            raise ValueError("Email message requires to_email.")
        if not self.text_body and not self.html_body:
            raise ValueError("Email message requires text_body or html_body.")


@dataclass(frozen=True)
class EmailResult:
    """Provider response for an email send attempt."""

    success: bool
    provider: str = ""
    message_id: str | None = None
    to: str | None = None
    sent_at: str | None = None
    error_code: int | None = None
    raw_response: Any | None = None
