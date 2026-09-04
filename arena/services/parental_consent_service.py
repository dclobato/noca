#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Guardian consent-revocation tokens and the three consent notifications.

LGPD art. 8 §5 grants the parent or legal guardian the right to withdraw consent at any
time. That right is exercised through a signed link, and this module owns both halves of
it: minting the link, and deciding whether a presented link may act.

**One live link at a time.** The token carries ``sub`` (the child's user id) and a ``gen``
claim bound to ``arena_users.consent_generation`` at minting time. Every consent
transition -- grant, revoke, guardian-email change, date-of-birth change -- bumps that
counter, so a link is invalidated by the next transition of any kind. That is what closes
replay (a used link cannot revoke twice) and what strips authority from a **former**
guardian once the address is changed.

**The link ships only after consent is granted**, in
``parental_consent_confirmed.jinja2``. A link minted before the grant could never work: it
would fail the consent check while consent is pending, and its generation would be stale
the moment the grant bumped the counter. So the consent invitation itself carries only
wording about the right, never a link.

Mail work lives here rather than in :mod:`arena.services.user_registration_service` (which
owns the signup/activation senders) so the revocation feature has one home and neither
that module nor ``auth_signup`` grows to carry it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.models.arena_users import ArenaUser
from arena.services.email_rendering import render_email
from arena.services.token_service import (
    _PARENTAL_REVOKE_TIMEOUT,
    ArenaTokenAction,
    JWTService,
)
from shared.age_check import AgeStatus, check_age
from shared.services.email_service import EmailService

logger = logging.getLogger(__name__)

REVOCATION_PATH = "/auth/parental-consent/revoke"
_GENERATION_CLAIM = "gen"


@dataclass(frozen=True)
class RevocationTokenResult:
    """Outcome of resolving a presented revocation token.

    Attributes:
        user: The child's account when the token may act, otherwise ``None``.
        log_reason: Why the token was refused, for the server log **only**. Every
            refusal must reach the caller as one indistinguishable failure page: telling
            an unknown ``sub`` apart from a stale generation would turn the link into an
            account oracle.
    """

    user: ArenaUser | None
    log_reason: str | None = None

    @property
    def valid(self) -> bool:
        """Return ``True`` when the token resolved to an account that may be revoked."""
        return self.user is not None


def build_revocation_url(url_base: str, token: str) -> str:
    """Build the absolute guardian revocation link for an email body.

    Args:
        url_base: Public base URL of the Arena deployment.
        token: Signed ``PARENTAL_CONSENT_REVOKE`` token.

    Returns:
        str: Absolute URL the guardian can open.
    """
    return f"{url_base.rstrip('/')}{REVOCATION_PATH}?token={token}"


def mint_revocation_token(usuario: ArenaUser, jwt_service: JWTService) -> str:
    """Mint a revocation token bound to the account's current consent epoch.

    Callers must mint **after** the consent transition has committed, so the token
    carries the post-transition generation rather than one the commit is about to
    invalidate.

    Args:
        usuario: Arena user whose guardian may revoke.
        jwt_service: Arena JWT service.

    Returns:
        str: Signed token carrying ``sub`` and the ``gen`` consent-epoch claim.
    """
    return str(
        jwt_service.criar(
            action=ArenaTokenAction.PARENTAL_CONSENT_REVOKE,
            sub=usuario.id,
            expires_in=_PARENTAL_REVOKE_TIMEOUT,
            extra_data={_GENERATION_CLAIM: usuario.consent_generation},
        )
    )


async def resolve_revocation_token(
    token: str,
    session: AsyncSession,
    jwt_service: JWTService,
    *,
    lock: bool = False,
) -> RevocationTokenResult:
    """Resolve a revocation token to the account it may suspend.

    The single gate for both the confirmation page and the revocation itself, so the two
    cannot disagree about what a link is allowed to do. It applies four rules:

    * the token is a valid, unexpired ``PARENTAL_CONSENT_REVOKE`` token naming a user;
    * its ``gen`` claim equals the stored ``consent_generation`` exactly;
    * consent is currently granted -- there is nothing to withdraw otherwise, and without
      this a link followed before any grant would suspend an account that was never
      active;
    * the child is still in the 13-17 consent band. An unknown date of birth refuses, and
      the link goes inert on its own the morning the child turns 18, with no scheduler and
      no stored expiry.

    Args:
        token: Presented token string.
        session: Active async database session.
        jwt_service: Arena JWT service.
        lock: When ``True``, take a row lock so the caller can re-validate and mutate
            without another request slipping between the check and the write.

    Returns:
        RevocationTokenResult: The account when every rule passes, otherwise a refusal
            whose reason is for logging only.
    """
    if not token:
        return RevocationTokenResult(user=None, log_reason="missing_token")

    claims = jwt_service.validar(token)
    if not claims.valid:
        return RevocationTokenResult(user=None, log_reason=f"invalid_token:{claims.reason}")
    if claims.action != ArenaTokenAction.PARENTAL_CONSENT_REVOKE:
        return RevocationTokenResult(user=None, log_reason="wrong_action")
    if claims.sub is None:
        return RevocationTokenResult(user=None, log_reason="missing_subject")

    generation = (claims.extra_data or {}).get(_GENERATION_CLAIM)
    # A bool is an int in Python; a JSON "true" must not pass for generation 1.
    if not isinstance(generation, int) or isinstance(generation, bool):
        return RevocationTokenResult(user=None, log_reason="missing_generation")

    query = select(ArenaUser).where(ArenaUser.id == claims.sub)
    if lock:
        query = query.with_for_update()
    usuario = (await session.execute(query)).scalar_one_or_none()
    if usuario is None:
        return RevocationTokenResult(user=None, log_reason="unknown_user")
    if generation != usuario.consent_generation:
        return RevocationTokenResult(user=None, log_reason="stale_generation")
    if not usuario.consentimento_responsavel:
        return RevocationTokenResult(user=None, log_reason="consent_not_granted")
    if usuario.dta_nascimento is None:
        return RevocationTokenResult(user=None, log_reason="unknown_date_of_birth")
    if check_age(usuario.dta_nascimento) is not AgeStatus.NEEDS_PARENTAL_CONSENT:
        return RevocationTokenResult(user=None, log_reason="outside_consent_age_band")

    return RevocationTokenResult(user=usuario)


async def send_consent_confirmed_email(
    usuario: ArenaUser,
    *,
    jwt_service: JWTService,
    email_service: EmailService,
    url_base: str,
    actor_key: str,
) -> bool:
    """Confirm a granted consent to the guardian and hand them the revocation link.

    This mail is load-bearing rather than a courtesy: it is the **only** carrier of the
    revocation link, so callers record a failed delivery at warning severity.

    It goes to the guardian alone. The child is told their consent state changed by other
    means; a child holding the revocation link could suspend their own account, and the
    right being exercised is the guardian's.

    Args:
        usuario: Arena user whose consent was just granted (already committed).
        jwt_service: Arena JWT service used to mint the revocation token.
        email_service: Configured email delivery service.
        url_base: Public base URL used to build the link.
        actor_key: Budget identity of the requester.

    Returns:
        bool: ``True`` when the message was accepted for delivery.
    """
    if not usuario.email_responsavel_legal:
        return False
    body = render_email(
        "parental_consent_confirmed.jinja2",
        nome=usuario.nome,
        revoke_url=build_revocation_url(url_base, mint_revocation_token(usuario, jwt_service)),
    )
    result = await email_service.send_email(
        to_email=usuario.email_responsavel_legal,
        to_name="Parent or legal guardian",
        subject=f"Consent confirmed for {settings.BRAND_NAME} account",
        text_body=body,
        actor_key=actor_key,
    )
    return result.success


async def send_consent_revoked_email(
    usuario: ArenaUser,
    *,
    recipient: str,
    email_service: EmailService,
    actor_key: str,
) -> bool:
    """Tell one party that the account was suspended by a consent withdrawal.

    Sent to the guardian and to the child separately, so that one recipient's provider
    refusal or spent budget cannot silence the other. The child must be told: their
    account stopped working, and they are entitled to know why.

    Args:
        usuario: Arena user whose consent was withdrawn.
        recipient: ``"guardian"`` or ``"child"``.
        email_service: Configured email delivery service.
        actor_key: Budget identity of the requester.

    Returns:
        bool: ``True`` when the message was accepted for delivery.
    """
    to_email = usuario.email_responsavel_legal if recipient == "guardian" else usuario.email_normalizado
    if not to_email:
        return False
    body = render_email("parental_consent_revoked.jinja2", nome=usuario.nome)
    result = await email_service.send_email(
        to_email=to_email,
        to_name="Parent or legal guardian" if recipient == "guardian" else usuario.nome,
        subject=f"{settings.BRAND_NAME} account suspended",
        text_body=body,
        actor_key=actor_key,
    )
    return result.success
