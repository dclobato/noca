#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Security event email notifications for both user-initiated and admin-initiated actions."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from arena.models.arena_users import ArenaUser
from shared.services.email_service import EmailService

logger = logging.getLogger(__name__)

_EMAIL_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "template" / "emails"


@lru_cache(maxsize=1)
def _email_template_environment() -> Environment:
    """Return the cached environment for plain-text Arena email templates."""
    return Environment(
        loader=FileSystemLoader(str(_EMAIL_TEMPLATE_DIR)),
        autoescape=False,
        trim_blocks=False,
        lstrip_blocks=False,
        undefined=StrictUndefined,
    )


def _render_email_template(template_name: str, **context: object) -> str:
    """Render a plain-text email template.

    Args:
        template_name: Filename inside ``arena/template/emails``.
        **context: Template variables.

    Returns:
        Rendered plain-text email body.
    """
    template = _email_template_environment().get_template(template_name)
    return template.render(**context).rstrip()


def send_password_changed_email(usuario: ArenaUser, email_service: EmailService) -> bool:
    """Notify a user that their password was changed.

    Args:
        usuario: Recipient Arena user.
        email_service: Configured email delivery service.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    body = _render_email_template("password_changed.jinja2", nome=usuario.nome)
    result = email_service.send_email(
        to_email=usuario.email,
        to_name=usuario.nome,
        subject="Your Arena password was changed",
        text_body=body,
    )
    return result.success


def send_2fa_enabled_email(usuario: ArenaUser, email_service: EmailService) -> bool:
    """Notify a user that two-factor authentication was enabled on their account.

    Args:
        usuario: Recipient Arena user.
        email_service: Configured email delivery service.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    body = _render_email_template("2fa_enabled.jinja2", nome=usuario.nome)
    result = email_service.send_email(
        to_email=usuario.email,
        to_name=usuario.nome,
        subject="Two-factor authentication enabled",
        text_body=body,
    )
    return result.success


def send_2fa_disabled_self_email(usuario: ArenaUser, email_service: EmailService) -> bool:
    """Notify a user that they disabled two-factor authentication on their account.

    Args:
        usuario: Recipient Arena user.
        email_service: Configured email delivery service.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    body = _render_email_template("2fa_disabled_self.jinja2", nome=usuario.nome)
    result = email_service.send_email(
        to_email=usuario.email,
        to_name=usuario.nome,
        subject="Two-factor authentication disabled",
        text_body=body,
    )
    return result.success


def send_backup_code_used_email(
    usuario: ArenaUser,
    email_service: EmailService,
    remaining: int,
) -> bool:
    """Notify a user that a backup code was consumed to access their account.

    Args:
        usuario: Recipient Arena user.
        email_service: Configured email delivery service.
        remaining: Number of backup codes still available after this use.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    body = _render_email_template(
        "backup_code_used.jinja2",
        nome=usuario.nome,
        remaining=str(remaining),
    )
    result = email_service.send_email(
        to_email=usuario.email,
        to_name=usuario.nome,
        subject="A backup code was used to access your account",
        text_body=body,
    )
    return result.success


def send_admin_2fa_disabled_email(usuario: ArenaUser, email_service: EmailService) -> bool:
    """Notify a user that an administrator disabled two-factor authentication.

    Args:
        usuario: Recipient Arena user.
        email_service: Configured email delivery service.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    body = _render_email_template("admin_2fa_disabled.jinja2", nome=usuario.nome)
    result = email_service.send_email(
        to_email=usuario.email,
        to_name=usuario.nome,
        subject="Two-factor authentication disabled",
        text_body=body,
    )
    return result.success


def send_admin_password_change_required_email(
    usuario: ArenaUser,
    email_service: EmailService,
) -> bool:
    """Notify a user that an administrator required a password change.

    Args:
        usuario: Recipient Arena user.
        email_service: Configured email delivery service.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    body = _render_email_template("admin_password_change_required.jinja2", nome=usuario.nome)
    result = email_service.send_email(
        to_email=usuario.email,
        to_name=usuario.nome,
        subject="Password change required",
        text_body=body,
    )
    return result.success


def send_ai_credits_topped_up_email(
    usuario: ArenaUser,
    email_service: EmailService,
    quantity: int,
    balance: int,
) -> bool:
    """Notify a user that AI review credits were added to their account.

    Args:
        usuario: Recipient Arena user.
        email_service: Configured email delivery service.
        quantity: Number of credits that were added.
        balance: New credit balance after the top-up.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    body = _render_email_template(
        "ai_credits_topped_up.jinja2",
        nome=usuario.nome,
        quantity=str(quantity),
        balance=str(balance),
    )
    result = email_service.send_email(
        to_email=usuario.email,
        to_name=usuario.nome,
        subject="AI review credits added to your account",
        text_body=body,
    )
    return result.success
