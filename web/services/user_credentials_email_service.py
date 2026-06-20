#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Helpers for composing and sending credential emails."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from shared.services.email_service import EmailService

_EMAIL_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "template" / "email"


@lru_cache(maxsize=1)
def _email_template_environment() -> Environment:
    """Return cached Jinja environment for plain-text email templates."""
    return Environment(
        loader=FileSystemLoader(str(_EMAIL_TEMPLATE_DIR)),
        autoescape=False,
        trim_blocks=False,
        lstrip_blocks=False,
        undefined=StrictUndefined,
    )


def _render_template(template_name: str, **context: str) -> str:
    """Render a named email template with the given context."""
    template = _email_template_environment().get_template(template_name)
    return template.render(**context).rstrip()


@dataclass(frozen=True)
class CredentialEmailContent:
    """Credential email payload."""

    subject: str
    text_body: str


@dataclass(frozen=True)
class CredentialEmailSendResult:
    """Result returned after an email send attempt."""

    success: bool
    detail: str


def build_user_credentials_email_content(
    *,
    fullname: str,
    contest_name: str,
    contest_login_url: str,
    username: str,
    password: str,
    sender_name: str,
) -> CredentialEmailContent:
    """Build subject and body for contest user credential emails.

    Args:
        fullname: Recipient full name.
        contest_name: Contest display name.
        contest_login_url: Contest login URL.
        username: User login identifier.
        password: Newly created password to deliver.
        sender_name: Signature sender name.

    Returns:
        A ready-to-send content object.
    """
    return CredentialEmailContent(
        subject=f"Your credentials for contest {contest_name}",
        text_body=_render_template(
            "send_credentials.jinja2",
            fullname=fullname,
            contest_name=contest_name,
            contest_login_url=contest_login_url,
            username=username,
            password=password,
            sender_name=sender_name,
        ),
    )


def build_uberadmin_credentials_email_content(
    *,
    fullname: str,
    login_url: str,
    username: str,
    password: str,
    sender_name: str,
) -> CredentialEmailContent:
    """Build subject and body for uberadmin credential emails.

    Args:
        fullname: Recipient full name.
        login_url: UberAdmin login URL.
        username: User login identifier.
        password: Newly created password to deliver.
        sender_name: Signature sender name.

    Returns:
        A ready-to-send content object.
    """
    return CredentialEmailContent(
        subject="Your Noca Contest global administrator credentials",
        text_body=_render_template(
            "send_uberadmin_credentials.jinja2",
            fullname=fullname,
            login_url=login_url,
            username=username,
            password=password,
            sender_name=sender_name,
        ),
    )


def send_credentials_email(
    email_service: EmailService,
    *,
    to_email: str,
    fullname: str,
    content: CredentialEmailContent,
) -> CredentialEmailSendResult:
    """Send a pre-built credential email.

    Args:
        email_service: Configured application email service.
        to_email: Recipient email address.
        fullname: Recipient full name (used as display name in To header).
        content: Pre-built subject and body from one of the ``build_*`` helpers.

    Returns:
        Result object describing success/failure for UI feedback.
    """
    try:
        result = email_service.send_email(
            to_email=to_email,
            to_name=fullname,
            subject=content.subject,
            text_body=content.text_body,
        )
    except Exception as exc:  # noqa: BLE001
        return CredentialEmailSendResult(success=False, detail=f"Failed to send credentials email: {exc}")

    if result.success:
        return CredentialEmailSendResult(success=True, detail="Credentials email sent successfully.")
    if result.error_code is not None:
        return CredentialEmailSendResult(
            success=False, detail=f"Failed to send credentials email (error code {result.error_code})."
        )
    return CredentialEmailSendResult(success=False, detail="Failed to send credentials email.")


def send_user_credentials_email(
    email_service: EmailService,
    *,
    to_email: str,
    fullname: str,
    contest_name: str,
    contest_login_url: str,
    username: str,
    password: str,
) -> CredentialEmailSendResult:
    """Build and send a credential email to one contest user.

    Args:
        email_service: Configured application email service.
        to_email: Recipient email address.
        fullname: Recipient full name.
        contest_name: Contest display name.
        contest_login_url: Contest login URL.
        username: Login username.
        password: Plaintext password to deliver.

    Returns:
        Result object describing success/failure for UI feedback.
    """
    content = build_user_credentials_email_content(
        fullname=fullname,
        contest_name=contest_name,
        contest_login_url=contest_login_url,
        username=username,
        password=password,
        sender_name=email_service.default_from_name or "Noca Contest",
    )
    return send_credentials_email(
        email_service,
        to_email=to_email,
        fullname=fullname,
        content=content,
    )
