#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Security event email notifications for both user-initiated and admin-initiated actions."""

from __future__ import annotations

import logging

from arena.models.arena_users import ArenaUser
from arena.services.email_rendering import render_email as _render_email_template
from shared.services.email_budget import EmailTier
from shared.services.email_providers import EmailProviderError
from shared.services.email_service import EmailService

logger = logging.getLogger(__name__)


async def _notify(
    usuario: ArenaUser,
    email_service: EmailService,
    subject: str,
    body: str,
    *,
    actor_key: str,
    tier: EmailTier,
) -> bool:
    """Send one notification, reporting provider failures instead of raising.

    These notifications are advisory: the action they describe is already
    committed, so a provider outage -- or a spent email budget, which is an
    ``EmailProviderError`` too -- must never fail the request that triggered
    them.

    Args:
        usuario: Recipient Arena user.
        email_service: Configured email delivery service.
        subject: Message subject line.
        body: Rendered plain-text body.
        actor_key: Budget identity of the actor causing this email.
        tier: Budget tier of that actor.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    try:
        result = await email_service.send_email(
            to_email=usuario.email,
            to_name=usuario.nome,
            subject=subject,
            text_body=body,
            actor_key=actor_key,
            tier=tier,
        )
    except EmailProviderError as exc:
        logger.warning("Notification email %r to user %s failed: %s", subject, usuario.id, exc)
        return False
    return result.success


async def send_password_changed_email(usuario: ArenaUser, email_service: EmailService) -> bool:
    """Notify a user that their password was changed.

    Args:
        usuario: Recipient Arena user.
        email_service: Configured email delivery service.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    body = _render_email_template("password_changed.jinja2", nome=usuario.nome)
    return await _notify(
        usuario, email_service, "Your Arena password was changed", body, actor_key=f"user:{usuario.id}", tier="user"
    )


async def send_google_linked_email(usuario: ArenaUser, email_service: EmailService) -> bool:
    """Notify a user that a Google account was linked to their account.

    Linking adds a second way into the account, so it is a security-relevant
    change and is announced like one -- the notification is what lets a user
    notice a link they did not make.

    Args:
        usuario: Recipient Arena user.
        email_service: Configured email delivery service.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    body = _render_email_template("google_account_linked.jinja2", nome=usuario.nome)
    return await _notify(
        usuario,
        email_service,
        "A Google account was linked to your account",
        body,
        actor_key=f"user:{usuario.id}",
        tier="user",
    )


async def send_google_unlinked_email(usuario: ArenaUser, email_service: EmailService) -> bool:
    """Notify a user that their linked Google account was removed.

    Args:
        usuario: Recipient Arena user.
        email_service: Configured email delivery service.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    body = _render_email_template("google_account_unlinked.jinja2", nome=usuario.nome)
    return await _notify(
        usuario,
        email_service,
        "Your linked Google account was removed",
        body,
        actor_key=f"user:{usuario.id}",
        tier="user",
    )


async def send_2fa_enabled_email(usuario: ArenaUser, email_service: EmailService) -> bool:
    """Notify a user that two-factor authentication was enabled on their account.

    Args:
        usuario: Recipient Arena user.
        email_service: Configured email delivery service.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    body = _render_email_template("2fa_enabled.jinja2", nome=usuario.nome)
    return await _notify(
        usuario, email_service, "Two-factor authentication enabled", body, actor_key=f"user:{usuario.id}", tier="user"
    )


async def send_2fa_disabled_self_email(usuario: ArenaUser, email_service: EmailService) -> bool:
    """Notify a user that they disabled two-factor authentication on their account.

    Args:
        usuario: Recipient Arena user.
        email_service: Configured email delivery service.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    body = _render_email_template("2fa_disabled_self.jinja2", nome=usuario.nome)
    return await _notify(
        usuario, email_service, "Two-factor authentication disabled", body, actor_key=f"user:{usuario.id}", tier="user"
    )


async def send_backup_code_used_email(
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
    return await _notify(
        usuario,
        email_service,
        "A backup code was used to access your account",
        body,
        actor_key=f"user:{usuario.id}",
        tier="user",
    )


async def send_admin_2fa_disabled_email(usuario: ArenaUser, email_service: EmailService, *, admin_id: str) -> bool:
    """Notify a user that an administrator disabled two-factor authentication.

    Args:
        usuario: Recipient Arena user.
        email_service: Configured email delivery service.
        admin_id: The acting administrator, whose email budget is charged.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    body = _render_email_template("admin_2fa_disabled.jinja2", nome=usuario.nome)
    return await _notify(
        usuario, email_service, "Two-factor authentication disabled", body, actor_key=f"user:{admin_id}", tier="admin"
    )


async def send_admin_google_unlinked_email(
    usuario: ArenaUser,
    email_service: EmailService,
    *,
    admin_id: str,
    password_reset_url: str | None,
    account_inactive: bool = False,
) -> bool:
    """Notify a user that an administrator unlinked their Google account.

    Args:
        usuario: Recipient Arena user.
        email_service: Configured email delivery service.
        admin_id: The acting administrator, whose email budget is charged.
        password_reset_url: Where to set a password, for an account that has
            none -- the message must tell such a user how to get back in,
            since Google was its only door. ``None`` for an account that can
            still sign in with its password.
        account_inactive: Whether the account is currently deactivated, in
            which case neither a password nor a reset is enough to sign in and
            the message must not suggest otherwise.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    body = _render_email_template(
        "admin_google_unlinked.jinja2",
        nome=usuario.nome,
        password_reset_url=password_reset_url,
        account_inactive=account_inactive,
    )
    return await _notify(
        usuario,
        email_service,
        "Your linked Google account was removed",
        body,
        actor_key=f"user:{admin_id}",
        tier="admin",
    )


async def send_admin_password_change_required_email(
    usuario: ArenaUser,
    email_service: EmailService,
    *,
    admin_id: str,
) -> bool:
    """Notify a user that an administrator required a password change.

    Args:
        usuario: Recipient Arena user.
        email_service: Configured email delivery service.
        admin_id: The acting administrator, whose email budget is charged.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    body = _render_email_template("admin_password_change_required.jinja2", nome=usuario.nome)
    return await _notify(
        usuario, email_service, "Password change required", body, actor_key=f"user:{admin_id}", tier="admin"
    )


async def send_ai_credits_topped_up_email(
    usuario: ArenaUser,
    email_service: EmailService,
    quantity: int,
    balance: int,
    *,
    admin_id: str,
) -> bool:
    """Notify a user that AI review credits were added to their account.

    Args:
        usuario: Recipient Arena user.
        email_service: Configured email delivery service.
        quantity: Number of credits that were added.
        balance: New credit balance after the top-up.
        admin_id: The acting administrator, whose email budget is charged.

    Returns:
        ``True`` when the provider reports successful delivery.
    """
    body = _render_email_template(
        "ai_credits_topped_up.jinja2",
        nome=usuario.nome,
        quantity=str(quantity),
        balance=str(balance),
    )
    return await _notify(
        usuario,
        email_service,
        "AI review credits added to your account",
        body,
        actor_key=f"user:{admin_id}",
        tier="admin",
    )
