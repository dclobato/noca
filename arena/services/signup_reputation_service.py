#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena signup reputation recording and admin-notification service.

Owns the post-signup flow that looks up the reputation of the signup IP and of
the new user's email through IPQualityScore, persists a per-user snapshot in
``arena_user_reputation``, and emails every ``ARENA_ADMIN`` a report about the
new account. The whole flow is a best-effort side channel: any failure is logged
and swallowed so it can never affect the account that was already created.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import UTC, datetime

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.email_templates import render_email
from arena.models.arena_user_reputation import ArenaUserReputation
from arena.models.arena_users import ArenaUser
from shared.enumerations import ArenaRole
from shared.services.email_reputation import EmailReputation, EmailReputationService
from shared.services.email_service import EmailService
from shared.services.network_utils.ip_reputation import IPQualityScoreIPReputationService, IPReputation

logger = logging.getLogger(__name__)


async def _load_or_create_row(session: AsyncSession, user_id: str) -> ArenaUserReputation:
    """Return the existing reputation row for a user, or a new unattached one."""
    existing = await session.scalar(select(ArenaUserReputation).where(ArenaUserReputation.user_id == user_id))
    if existing is not None:
        return existing
    row = ArenaUserReputation(user_id=user_id)
    session.add(row)
    return row


async def _persist_reputation(
    session: AsyncSession,
    *,
    user_id: str,
    signup_ip: str | None,
    ip_rep: IPReputation | None,
    email_rep: EmailReputation | None,
) -> None:
    """Insert or update the per-user reputation snapshot and commit."""
    now = datetime.now(UTC)
    row = await _load_or_create_row(session, user_id)
    if signup_ip is not None:
        row.signup_ip = signup_ip
    if ip_rep is not None:
        row.ip_fraud_score = ip_rep.fraud_score
        row.ip_report = dataclasses.asdict(ip_rep)
        row.ip_checked_at = now
    if email_rep is not None:
        row.email_fraud_score = email_rep.fraud_score
        row.email_overall_score = email_rep.overall_score
        row.email_report = dataclasses.asdict(email_rep)
        row.email_checked_at = now
    await session.commit()


def _yes_no(value: bool) -> str:
    """Return the report's display value for a boolean reputation signal."""
    return "yes" if value else "no"


def _render_ip_reputation_section(ip_rep: IPReputation | None) -> str:
    """Build the display-ready IP reputation section."""
    if ip_rep is None:
        return "  Not available."
    return "\n".join(
        (
            f"  Fraud score:  {ip_rep.fraud_score}/100",
            f"  Proxy:        {_yes_no(ip_rep.proxy)}",
            f"  VPN:          {_yes_no(ip_rep.vpn)} (active: {_yes_no(ip_rep.active_vpn)})",
            f"  Tor:          {_yes_no(ip_rep.tor)} (active: {_yes_no(ip_rep.active_tor)})",
            f"  Recent abuse: {_yes_no(ip_rep.recent_abuse)}",
            f"  Crawler:      {_yes_no(ip_rep.is_crawler)}",
            f"  Mobile:       {_yes_no(ip_rep.mobile)}",
        )
    )


def _render_email_reputation_section(email_rep: EmailReputation | None) -> str:
    """Build the display-ready email reputation section."""
    if email_rep is None:
        return "  Not available."
    return "\n".join(
        (
            f"  Fraud score:   {email_rep.fraud_score}/100",
            f"  Overall score: {email_rep.overall_score}/4",
            f"  Valid:         {_yes_no(email_rep.valid)}",
            f"  Disposable:    {_yes_no(email_rep.disposable)}",
            f"  Suspect:       {_yes_no(email_rep.suspect)}",
            f"  Common:        {_yes_no(email_rep.common)}",
        )
    )


def _render_admin_email(
    *,
    user: ArenaUser,
    signup_ip: str | None,
    ip_rep: IPReputation | None,
    email_rep: EmailReputation | None,
) -> tuple[str, str]:
    """Render the subject and plain-text body for a new-signup notification."""
    email_content = render_email(
        "new_user_reputation",
        nome=user.nome,
        email=user.email_normalizado,
        user_id=user.id,
        signup_ip=signup_ip or "unknown",
        ip_reputation_section=_render_ip_reputation_section(ip_rep),
        email_reputation_section=_render_email_reputation_section(email_rep),
    )
    return email_content.subject, email_content.body


async def _notify_admins(
    session: AsyncSession,
    *,
    user: ArenaUser,
    signup_ip: str | None,
    ip_rep: IPReputation | None,
    email_rep: EmailReputation | None,
    email_service: EmailService,
) -> None:
    """Email every ARENA_ADMIN a reputation report about the new user."""
    admins = (await session.scalars(select(ArenaUser).where(ArenaUser.role == ArenaRole.ARENA_ADMIN))).all()
    if not admins:
        return
    subject, body = _render_admin_email(user=user, signup_ip=signup_ip, ip_rep=ip_rep, email_rep=email_rep)
    # System-originated fan-out: no actor to budget, so ``actor_key`` stays None.
    for admin in admins:
        try:
            await email_service.send_email(
                to_email=admin.email_normalizado,
                to_name=admin.nome,
                subject=subject,
                text_body=body,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to send signup reputation email to admin %s: %s", admin.id, exc)


async def record_signup_reputation(
    session: AsyncSession,
    *,
    user_id: str,
    email: str,
    ip_address: str | None,
    ip_service: IPQualityScoreIPReputationService,
    email_reputation_service: EmailReputationService,
    email_service: EmailService,
    reputation_enabled: bool,
) -> None:
    """Record, and optionally report, the reputation of a new Arena signup.

    Intended to run as a background task after the signup response. The signup IP
    is **always** persisted so it can be scored later by the backfill script even
    when the integration is disabled at signup time. When ``reputation_enabled``
    is set, the blocking IPQualityScore lookups run in a worker thread and every
    ``ARENA_ADMIN`` is emailed a report. Every failure mode is contained: a
    missing user, a failed lookup, or an email error is logged and never
    propagated.

    Args:
        session: A fresh async database session owned by the caller.
        user_id: ID of the newly created Arena user.
        email: The user's submitted email address.
        ip_address: The client IP captured at signup, if any.
        ip_service: IPQualityScore IP reputation service.
        email_reputation_service: IPQualityScore email reputation service.
        email_service: Email delivery service used for the admin notification.
        reputation_enabled: Whether the IPQualityScore integration is configured;
            when False, only the signup IP is stored (no lookups, no admin email).
    """
    try:
        ip_rep: IPReputation | None = None
        email_rep: EmailReputation | None = None
        if reputation_enabled:
            if ip_address is not None:
                ip_rep = await anyio.to_thread.run_sync(ip_service.check, ip_address)
            email_rep = await anyio.to_thread.run_sync(email_reputation_service.check, email)

        await _persist_reputation(
            session,
            user_id=user_id,
            signup_ip=ip_address,
            ip_rep=ip_rep,
            email_rep=email_rep,
        )

        if not reputation_enabled:
            return

        user = await session.get(ArenaUser, user_id)
        if user is None:
            logger.warning("Signup reputation: user %s no longer exists; skipping admin email", user_id)
            return
        await _notify_admins(
            session,
            user=user,
            signup_ip=ip_address,
            ip_rep=ip_rep,
            email_rep=email_rep,
            email_service=email_service,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Signup reputation flow failed for user %s: %s", user_id, exc)
        await session.rollback()
