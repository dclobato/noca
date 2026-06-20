#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
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
    _utcnow,
    confirmar_email,
)
from shared.services.email_service import EmailService
from shared.services.email_validation import EmailValidationService

logger = logging.getLogger(__name__)


async def revalidar_email(
    user_id: str,
    session: AsyncSession,
    jwt_service: JWTService,
    email_service: EmailService,
    url_base: str,
) -> UserServiceResult:
    """Re-send the email confirmation link for an unconfirmed account.

    Args:
        user_id: UUID string of the target user.
        session: Active async database session.
        jwt_service: Arena JWT service for token creation.
        email_service: Email delivery service.
        url_base: Base URL used to build the confirmation link.

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
    sent = _enviar_email_confirmacao(usuario, token, email_service, url_base)
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
) -> UserServiceResult:
    """Re-send the parental consent link for a pending account.

    Args:
        user_id: UUID string of the target user.
        session: Active async database session.
        jwt_service: Arena JWT service for token creation.
        email_service: Email delivery service.
        url_base: Base URL used to build the consent link.

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
    sent = _enviar_email_consentimento_responsavel_interno(usuario, token, email_service, url_base)
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
) -> UserServiceResult:
    """Store a parent/legal guardian email and send a consent link.

    Args:
        user_id: UUID string of the target user.
        email_responsavel_legal: New guardian email address.
        session: Active async database session.
        jwt_service: Arena JWT service for token creation.
        email_service: Email delivery service.
        url_base: Base URL used to build the consent link.

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
    await session.flush()
    return await revalidar_consentimento_responsavel(
        user_id=usuario.id,
        session=session,
        jwt_service=jwt_service,
        email_service=email_service,
        url_base=url_base,
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


async def validar_consentimento_responsavel_por_token(
    token: str,
    session: AsyncSession,
    jwt_service: JWTService,
) -> UserServiceResult:
    """Confirm parental consent using the JWT sent to the guardian.

    Args:
        token: PARENTAL_CONSENT JWT sent in the consent email.
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
    if claims.action != ArenaTokenAction.PARENTAL_CONSENT:
        return UserServiceResult(status=UserOperationStatus.INVALID_TOKEN, error_message="Wrong token action")
    if claims.sub is None:
        return UserServiceResult(status=UserOperationStatus.INVALID_TOKEN, error_message="Missing user id")
    result = await session.execute(select(ArenaUser).where(ArenaUser.id == claims.sub))
    usuario = result.scalar_one_or_none()
    if usuario is None:
        return UserServiceResult(status=UserOperationStatus.USER_NOT_FOUND, error_message="User not found")
    if usuario.consentimento_responsavel:
        return UserServiceResult(status=UserOperationStatus.SUCCESS, user=usuario)
    usuario.consentimento_responsavel = True
    usuario.dta_consentimento_responsavel = _utcnow()
    await session.flush()
    logger.info("Parental consent confirmed via token for %s", usuario.email)
    return UserServiceResult(status=UserOperationStatus.SUCCESS, user=usuario)
