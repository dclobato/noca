#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Helpers for composing and sending credential emails."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from shared.services.email_providers import EmailBudgetExceeded
from shared.services.email_service import QUEUE_PROVIDER_NAME, EmailService
from web.config import settings
from web.models.users import UberAdmin, User

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
    """Render a named email template with ``brand_name`` always injected."""
    render_context: dict[str, str] = {"brand_name": settings.BRAND_NAME}
    render_context.update(context)
    template = _email_template_environment().get_template(template_name)
    return template.render(**render_context).rstrip()


@dataclass(frozen=True)
class CredentialEmailContent:
    """Credential email payload."""

    subject: str
    text_body: str


@dataclass(frozen=True)
class CredentialEmailSendResult:
    """Result returned after an email send attempt.

    Attributes:
        success: The message was accepted -- delivered, or handed to the mailer.
        detail: Human-readable outcome for the UI.
        queued: ``True`` when the message went to the mailer queue rather than out the door.
        budget_exceeded: ``True`` when the actor's email budget refused it; ``retry_after_seconds``
            then says when the window reopens. A batch caller stops at the first such result.
    """

    success: bool
    detail: str
    queued: bool = False
    budget_exceeded: bool = False
    retry_after_seconds: int = 0


def build_animator_credential_email_content(
    *,
    fullname: str,
    contest_name: str,
    label: str,
    scope_label: str,
    token: str,
) -> CredentialEmailContent:
    """Build subject and body for an animator operator credential email.

    Args:
        fullname: Recipient full name.
        contest_name: Contest display name.
        label: Human-readable credential label.
        scope_label: Global scope or the authorized site name.
        token: One-time plaintext operator token.

    Returns:
        A ready-to-send content object.
    """
    return CredentialEmailContent(
        subject=f"Animator credential for contest {contest_name}",
        text_body=_render_template(
            "animator_credential.jinja2",
            fullname=fullname,
            contest_name=contest_name,
            label=label,
            scope_label=scope_label,
            token=token,
            sender_name=settings.BRAND_NAME,
        ),
    )


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
        subject=f"Your {settings.BRAND_NAME} global administrator credentials",
        text_body=_render_template(
            "send_uberadmin_credentials.jinja2",
            fullname=fullname,
            login_url=login_url,
            username=username,
            password=password,
            sender_name=sender_name,
        ),
    )


async def send_credentials_email(
    email_service: EmailService,
    *,
    to_email: str,
    fullname: str,
    content: CredentialEmailContent,
    actor_key: str,
) -> CredentialEmailSendResult:
    """Send a pre-built credential email on behalf of an admin actor.

    Args:
        email_service: Configured application email service.
        to_email: Recipient email address.
        fullname: Recipient full name (used as display name in To header).
        content: Pre-built subject and body from one of the ``build_*`` helpers.
        actor_key: Budget identity of the admin sending it (``admin:<id>`` / ``uberadmin:<id>``).

    Returns:
        Result object describing success/failure for UI feedback.
    """
    try:
        result = await email_service.send_email(
            to_email=to_email,
            to_name=fullname,
            subject=content.subject,
            text_body=content.text_body,
            actor_key=actor_key,
            tier="admin",
        )
    except EmailBudgetExceeded as exc:
        return CredentialEmailSendResult(
            success=False,
            detail=f"Email budget exceeded; try again in {exc.retry_after_seconds} s.",
            budget_exceeded=True,
            retry_after_seconds=exc.retry_after_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        return CredentialEmailSendResult(success=False, detail=f"Failed to send credentials email: {exc}")

    if result.success and result.provider == QUEUE_PROVIDER_NAME:
        return CredentialEmailSendResult(success=True, detail="Credentials email queued for delivery.", queued=True)
    if result.success:
        return CredentialEmailSendResult(success=True, detail="Credentials email sent successfully.")
    if result.error_code is not None:
        return CredentialEmailSendResult(
            success=False, detail=f"Failed to send credentials email (error code {result.error_code})."
        )
    return CredentialEmailSendResult(success=False, detail="Failed to send credentials email.")


async def send_user_credentials_email(
    email_service: EmailService,
    *,
    to_email: str,
    fullname: str,
    contest_name: str,
    contest_login_url: str,
    username: str,
    password: str,
    actor_key: str,
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
        actor_key: Budget identity of the admin sending it.

    Returns:
        Result object describing success/failure for UI feedback.
    """
    content = build_user_credentials_email_content(
        fullname=fullname,
        contest_name=contest_name,
        contest_login_url=contest_login_url,
        username=username,
        password=password,
        sender_name=email_service.default_from_name or settings.BRAND_NAME,
    )
    return await send_credentials_email(
        email_service,
        to_email=to_email,
        fullname=fullname,
        content=content,
        actor_key=actor_key,
    )


def email_actor_key(actor: User | UberAdmin) -> str:
    """Return the email-budget identity of a Web actor.

    The two id spaces are both UUIDs, but naming the kind keeps a contest admin
    and an uberadmin from ever sharing a window by coincidence, and makes the
    key legible in the budget's Valkey keys and logs.
    """
    kind = "uberadmin" if isinstance(actor, UberAdmin) else "user"
    return f"{kind}:{actor.id}"
