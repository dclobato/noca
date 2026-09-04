#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena user email/consent token validation and revalidation flows.

Handles re-sending confirmation emails, processing JWT confirmation links, and
parental consent management. For the initial registration flow, see
:mod:`arena.services.user_registration_service`.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_users import ArenaUser
from arena.services.token_service import ArenaTokenAction, JWTService
from arena.services.user_registration_service import (
    _enviar_email_confirmacao,
    _enviar_email_consentimento_responsavel_interno,
    _gerar_token_confirmacao_email,
    _gerar_token_consentimento_responsavel,
)
from arena.services.user_service import (
    UserOperationStatus,
    UserServiceResult,
    confirmar_email,
    grant_parental_consent,
)
from shared.age_check import AgeStatus, check_age
from shared.services.email_service import EmailService
from shared.services.email_validation import EmailValidationService

logger = logging.getLogger(__name__)


async def revalidar_email(
    user_id: str,
    session: AsyncSession,
    jwt_service: JWTService,
    email_service: EmailService,
    url_base: str,
    *,
    actor_key: str,
) -> UserServiceResult:
    """Re-send the email confirmation link for an unconfirmed account.

    Args:
        user_id: UUID string of the target user.
        session: Active async database session.
        jwt_service: Arena JWT service for token creation.
        email_service: Email delivery service.
        url_base: Base URL used to build the confirmation link.
        actor_key: Budget identity of the requester (the client IP).

    Returns:
        UserServiceResult: ``SUCCESS`` when the email was dispatched.
    """
    result = await session.execute(select(ArenaUser).where(ArenaUser.id == user_id))
    usuario = result.scalar_one_or_none()
    if usuario is None:
        return UserServiceResult(status=UserOperationStatus.USER_NOT_FOUND, error_message="User not found")
    if usuario.email_confirmado:
        return UserServiceResult(
            status=UserOperationStatus.EMAIL_ALREADY_CONFIRMED,
            user=usuario,
            error_message="Email is already confirmed",
        )
    token = _gerar_token_confirmacao_email(usuario, jwt_service)
    sent = await _enviar_email_confirmacao(usuario, token, email_service, url_base, actor_key=actor_key)
    if not sent:
        return UserServiceResult(
            status=UserOperationStatus.SEND_EMAIL_ERROR,
            user=usuario,
            token=token,
            error_message="Failed to send confirmation email",
        )
    logger.info("Re-sent confirmation email to %s", usuario.email)
    return UserServiceResult(status=UserOperationStatus.SUCCESS, user=usuario, token=token, email_sent=True)


async def revalidar_consentimento_responsavel(
    user_id: str,
    session: AsyncSession,
    jwt_service: JWTService,
    email_service: EmailService,
    url_base: str,
    *,
    actor_key: str,
) -> UserServiceResult:
    """Re-send the parental consent link for a pending account.

    Args:
        user_id: UUID string of the target user.
        session: Active async database session.
        jwt_service: Arena JWT service for token creation.
        email_service: Email delivery service.
        url_base: Base URL used to build the consent link.
        actor_key: Budget identity of the requester (the client IP).

    Returns:
        UserServiceResult: ``SUCCESS`` on dispatch or if consent is already given.
    """
    result = await session.execute(select(ArenaUser).where(ArenaUser.id == user_id))
    usuario = result.scalar_one_or_none()
    if usuario is None:
        return UserServiceResult(status=UserOperationStatus.USER_NOT_FOUND, error_message="User not found")
    if usuario.consentimento_responsavel:
        return UserServiceResult(status=UserOperationStatus.SUCCESS, user=usuario, email_sent=False)
    if not usuario.email_responsavel_legal:
        return UserServiceResult(
            status=UserOperationStatus.INVALID_EMAIL,
            user=usuario,
            error_message="Parent/legal guardian email is missing",
        )
    token = _gerar_token_consentimento_responsavel(usuario, jwt_service)
    sent = await _enviar_email_consentimento_responsavel_interno(
        usuario, token, email_service, url_base, actor_key=actor_key
    )
    if not sent:
        return UserServiceResult(
            status=UserOperationStatus.SEND_EMAIL_ERROR,
            user=usuario,
            token=token,
            error_message="Failed to send parental consent email",
        )
    return UserServiceResult(status=UserOperationStatus.SUCCESS, user=usuario, token=token, email_sent=True)


async def atualizar_email_responsavel(
    user_id: str,
    email_responsavel_legal: str,
    session: AsyncSession,
    jwt_service: JWTService,
    email_service: EmailService,
    url_base: str,
    *,
    actor_key: str,
) -> UserServiceResult:
    """Store a parent/legal guardian email and send a consent link.

    Bumps ``consent_generation``, which is what strips authority from the **previous**
    guardian: every revocation link minted for them becomes inert. That bump is the whole
    security effect of this path, and deliberately the only one. The account is not
    deactivated and its sessions are not invalidated, because there is nothing here to
    suspend -- this route is reachable only from the pending-parental login flow, so
    consent is already withheld, and the consent gate already refuses both a new login and
    a live session on every request.

    Args:
        user_id: UUID string of the target user.
        email_responsavel_legal: New guardian email address.
        session: Active async database session.
        jwt_service: Arena JWT service for token creation.
        email_service: Email delivery service.
        url_base: Base URL used to build the consent link.
        actor_key: Budget identity of the requester (the client IP).

    Returns:
        UserServiceResult: ``SUCCESS`` on dispatch, or an error status.
    """
    result = await session.execute(select(ArenaUser).where(ArenaUser.id == user_id))
    usuario = result.scalar_one_or_none()
    if usuario is None:
        return UserServiceResult(status=UserOperationStatus.USER_NOT_FOUND, error_message="User not found")
    try:
        normalized = EmailValidationService.normalize(email_responsavel_legal)
    except ValueError:
        return UserServiceResult(status=UserOperationStatus.INVALID_EMAIL, user=usuario)
    usuario.email_responsavel_legal = normalized
    usuario.consentimento_responsavel = False
    usuario.dta_consentimento_responsavel = None
    usuario.consent_generation += 1
    await session.flush()
    return await revalidar_consentimento_responsavel(
        user_id=usuario.id,
        session=session,
        jwt_service=jwt_service,
        email_service=email_service,
        url_base=url_base,
        actor_key=actor_key,
    )


async def validar_email_por_token(
    token: str,
    session: AsyncSession,
    jwt_service: JWTService,
) -> UserServiceResult:
    """Confirm a user's email address using the JWT from the confirmation link.

    Args:
        token: VALIDATE_EMAIL JWT sent in the confirmation email.
        session: Active async database session.
        jwt_service: Arena JWT service for token validation.

    Returns:
        UserServiceResult: ``SUCCESS`` with the user on confirmation, or an
            error status.
    """
    claims = jwt_service.validar(token)
    if not claims.valid:
        if claims.reason == "expired":
            return UserServiceResult(status=UserOperationStatus.TOKEN_EXPIRED, error_message="Token expired")
        return UserServiceResult(
            status=UserOperationStatus.INVALID_TOKEN,
            error_message=f"Invalid token: {claims.reason}",
        )
    if claims.action != ArenaTokenAction.VALIDATE_EMAIL:
        return UserServiceResult(status=UserOperationStatus.INVALID_TOKEN, error_message="Wrong token action")
    result = await session.execute(select(ArenaUser).where(ArenaUser.email_normalizado == claims.sub))
    usuario = result.scalar_one_or_none()
    if usuario is None:
        return UserServiceResult(status=UserOperationStatus.USER_NOT_FOUND, error_message="User not found")
    if usuario.email_confirmado:
        return UserServiceResult(
            status=UserOperationStatus.EMAIL_ALREADY_CONFIRMED,
            user=usuario,
            error_message="Email already confirmed",
        )
    await confirmar_email(usuario, session)
    logger.info("Email confirmed via token for %s", usuario.email)
    return UserServiceResult(status=UserOperationStatus.SUCCESS, user=usuario)


async def resolver_consentimento_por_token(
    token: str,
    session: AsyncSession,
    jwt_service: JWTService,
    *,
    lock: bool = False,
) -> UserServiceResult:
    """Resolve a parental-consent grant token to its account, mutating nothing.

    The single validation gate for both halves of the grant flow: the ``GET`` review
    page calls it unlocked to decide what to render, and the ``POST`` re-runs it under
    a row lock (through :func:`validar_consentimento_responsavel_por_token`) before
    granting, so the page and the action can never disagree about whether a link may
    act, and the ``POST`` never trusts a validation the ``GET`` performed.

    One age rule applies here and deliberately nothing more: a token naming an account
    currently in the **blocked** under-13 band is refused, because a stale link could
    otherwise restore the consent flag that a date-of-birth change to under 13 cleared
    -- and ``ativar_conta_se_pronta`` tests only email and consent, so that restore
    would re-activate a prohibited account. An *adult* account still resolves: the
    grant is surplus for an adult but harmless, and this link is the only self-service
    recovery for an account whose holder turned 18 while consent was still pending
    (that state falls out of the pending-parental login screen, yet activation keeps
    demanding the consent flag). The token carries no consent-epoch claim -- the epoch
    binds *revocation* links; a grant link is bounded by its own expiry instead.

    Args:
        token: PARENTAL_CONSENT JWT sent in the consent email.
        session: Active async database session.
        jwt_service: Arena JWT service for token validation.
        lock: When ``True``, take a row lock so the caller can re-validate and mutate
            without another request slipping between the check and the write.

    Returns:
        UserServiceResult: ``SUCCESS`` with the user when the link may act, or an
            error status. Never mutates the account.
    """
    claims = jwt_service.validar(token)
    if not claims.valid:
        if claims.reason == "expired":
            return UserServiceResult(status=UserOperationStatus.TOKEN_EXPIRED, error_message="Token expired")
        return UserServiceResult(
            status=UserOperationStatus.INVALID_TOKEN,
            error_message=f"Invalid token: {claims.reason}",
        )
    if claims.action != ArenaTokenAction.PARENTAL_CONSENT:
        return UserServiceResult(status=UserOperationStatus.INVALID_TOKEN, error_message="Wrong token action")
    if claims.sub is None:
        return UserServiceResult(status=UserOperationStatus.INVALID_TOKEN, error_message="Missing user id")
    query = select(ArenaUser).where(ArenaUser.id == claims.sub)
    if lock:
        query = query.with_for_update()
    result = await session.execute(query)
    usuario = result.scalar_one_or_none()
    if usuario is None:
        return UserServiceResult(status=UserOperationStatus.USER_NOT_FOUND, error_message="User not found")
    if usuario.dta_nascimento is not None and check_age(usuario.dta_nascimento) is AgeStatus.BLOCKED:
        return UserServiceResult(
            status=UserOperationStatus.INVALID_TOKEN,
            error_message="Account holder is under the minimum age",
        )
    return UserServiceResult(status=UserOperationStatus.SUCCESS, user=usuario)


async def validar_consentimento_responsavel_por_token(
    token: str,
    session: AsyncSession,
    jwt_service: JWTService,
) -> UserServiceResult:
    """Confirm parental consent using the JWT sent to the guardian.

    Resolves the token through :func:`resolver_consentimento_por_token` **under a row
    lock** and grants inside it, so two guardians clicking the same link at once cannot
    both observe ``transitioned=True`` and have the caller send two confirmation emails
    carrying two revocation links.

    Args:
        token: PARENTAL_CONSENT JWT sent in the consent email.
        session: Active async database session.
        jwt_service: Arena JWT service for token validation.

    Returns:
        UserServiceResult: ``SUCCESS`` with the user on confirmation, or an
            error status. On ``SUCCESS``, ``extra_data["transitioned"]`` reports whether
            this call actually granted consent; it is ``False`` for a re-opened link, and
            the caller must not notify or audit in that case.
    """
    resolved = await resolver_consentimento_por_token(token, session, jwt_service, lock=True)
    if resolved.status != UserOperationStatus.SUCCESS or resolved.user is None:
        return resolved
    transitioned = await grant_parental_consent(resolved.user, session)
    if transitioned:
        logger.info("Parental consent confirmed via token for %s", resolved.user.email)
    return UserServiceResult(
        status=UserOperationStatus.SUCCESS,
        user=resolved.user,
        extra_data={"transitioned": transitioned},
    )
