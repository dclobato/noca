#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena user account lifecycle service.

Handles account activation/deactivation, email confirmation, session
invalidation, and related account-state mutations.

For registration and email delivery, see
:mod:`arena.services.user_registration_service`. For email/consent token
validation, see :mod:`arena.services.user_email_service`. For AI credit
operations, see :mod:`arena.services.user_ai_credit_service`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_users import ArenaUser
from shared.age_check import AgeStatus, check_age

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


class UserOperationStatus(Enum):
    """Outcome codes for user account operations.

    Attributes:
        SUCCESS: Operation completed without error.
        USER_NOT_FOUND: No user matched the given identifier.
        EMAIL_ALREADY_CONFIRMED: Email is already verified; no action taken.
        USER_INACTIVE: Account exists but is deactivated.
        INVALID_TOKEN: JWT is expired, malformed, or has the wrong action.
        TOKEN_EXPIRED: JWT has expired (specific sub-case of INVALID_TOKEN).
        INVALID_CREDENTIALS: Supplied credentials are wrong.
        SEND_EMAIL_ERROR: Email dispatch failed.
        DATABASE_ERROR: An unexpected database error occurred.
        USER_ALREADY_REGISTERED: The email address is already in use.
        INVALID_EMAIL: The supplied email address is not valid.
        PARENTAL_CONSENT_REQUIRED: The account requires parent/legal guardian consent.
        AGE_RECONFIRMATION_REQUIRED: The account needs date-of-birth regularisation.
        UNDERAGE_BLOCKED: The user is younger than the minimum allowed age.
        UNKNOWN: An unexpected error occurred.
    """

    SUCCESS = 0
    USER_NOT_FOUND = 1
    EMAIL_ALREADY_CONFIRMED = 2
    USER_INACTIVE = 3
    INVALID_TOKEN = 4
    TOKEN_EXPIRED = 5
    INVALID_CREDENTIALS = 6
    SEND_EMAIL_ERROR = 7
    DATABASE_ERROR = 8
    USER_ALREADY_REGISTERED = 9
    INVALID_EMAIL = 10
    PARENTAL_CONSENT_REQUIRED = 11
    AGE_RECONFIRMATION_REQUIRED = 12
    UNDERAGE_BLOCKED = 13
    UNKNOWN = 99


@dataclass
class UserServiceResult:
    """Unified result type for user account operations.

    Attributes:
        status: Outcome of the operation.
        user: The affected ``ArenaUser``, when available.
        error_message: Human-readable description of the failure.
        token: Short-lived JWT produced during registration or email flows.
        email_sent: ``True`` when a confirmation/notification email was sent.
        extra_data: Arbitrary additional data (e.g. token claims).
    """

    status: UserOperationStatus
    user: ArenaUser | None = None
    error_message: str | None = None
    token: str | None = None
    email_sent: bool = False
    extra_data: dict[str, Any] | None = None


async def regularizar_data_nascimento(
    user_id: str,
    dta_nascimento: date,
    session: AsyncSession,
) -> UserServiceResult:
    """Store a missing date of birth and apply age-gate defaults.

    Args:
        user_id: UUID string of the target user.
        dta_nascimento: Date of birth to store.
        session: Active async database session.

    Returns:
        UserServiceResult: ``SUCCESS``, ``UNDERAGE_BLOCKED``, or
            ``USER_NOT_FOUND``.
    """
    result = await session.execute(select(ArenaUser).where(ArenaUser.id == user_id))
    usuario = result.scalar_one_or_none()
    if usuario is None:
        return UserServiceResult(status=UserOperationStatus.USER_NOT_FOUND, error_message="User not found")
    status = check_age(dta_nascimento)
    if status == AgeStatus.BLOCKED:
        usuario.ativo = False
        await session.flush()
        return UserServiceResult(status=UserOperationStatus.UNDERAGE_BLOCKED, user=usuario)
    usuario.dta_nascimento = dta_nascimento
    if status == AgeStatus.ALLOWED:
        usuario.consentimento_responsavel = True
        usuario.dta_consentimento_responsavel = _utcnow()
    else:
        usuario.consentimento_responsavel = False
        usuario.dta_consentimento_responsavel = None
    await session.flush()
    return UserServiceResult(status=UserOperationStatus.SUCCESS, user=usuario)


async def update_date_of_birth(
    usuario: ArenaUser,
    date_of_birth: date,
    session: AsyncSession,
) -> AgeStatus:
    """Update a date of birth and apply the Arena age policy.

    The parental-consent fields are left unchanged for adults because they are
    not part of the adult access gate.

    Args:
        usuario: Arena user whose date of birth is being changed.
        date_of_birth: Replacement date of birth.
        session: Active async database session.

    Returns:
        Resulting age-policy status.

    Raises:
        ValueError: If the date is in the future.
    """
    if date_of_birth > _utcnow().date():
        raise ValueError("Date of birth cannot be in the future.")

    if usuario.dta_nascimento == date_of_birth:
        return check_age(date_of_birth)

    usuario.dta_nascimento = date_of_birth
    status = check_age(date_of_birth)

    if status == AgeStatus.BLOCKED:
        await desativar_conta(usuario, session)

    if status in {AgeStatus.BLOCKED, AgeStatus.NEEDS_PARENTAL_CONSENT}:
        usuario.consentimento_responsavel = False
        usuario.dta_consentimento_responsavel = None
        await invalidate_sessions(usuario, session)

    await session.flush()
    logger.warning("Updated date of birth for %s (age_status=%s)", usuario.email, status.name)
    return status


async def ativar_conta(usuario: ArenaUser, session: AsyncSession) -> bool:
    """Activate a previously inactive account.

    Args:
        usuario: Arena user to activate.
        session: Active async database session.

    Returns:
        bool: ``True`` (including if already active).
    """
    if usuario.ativo:
        return True
    usuario.ativo = True
    usuario.dta_ativacao_conta = _utcnow()
    await session.flush()
    logger.info("Activated account for %s", usuario.email)
    return True


async def ativar_conta_se_pronta(usuario: ArenaUser, session: AsyncSession) -> bool:
    """Activate the account only when email and parental consent gates are clear.

    Args:
        usuario: Arena user to conditionally activate.
        session: Active async database session.

    Returns:
        bool: ``True`` when the account was activated, ``False`` when gates are pending.
    """
    if not usuario.email_confirmado or not usuario.consentimento_responsavel:
        return False
    return await ativar_conta(usuario, session)


async def confirmar_email(usuario: ArenaUser, session: AsyncSession) -> bool:
    """Mark a user's email as confirmed.

    Args:
        usuario: Arena user whose email is being confirmed.
        session: Active async database session.

    Returns:
        bool: ``True`` (including if already confirmed).
    """
    if usuario.email_confirmado:
        return True
    usuario.email_confirmado = True
    usuario.dta_validacao_email = _utcnow()
    await session.flush()
    return True


async def desativar_conta(usuario: ArenaUser, session: AsyncSession) -> bool:
    """Deactivate a user account.

    Args:
        usuario: Arena user to deactivate.
        session: Active async database session.

    Returns:
        bool: ``True`` (including if already inactive).
    """
    if not usuario.ativo:
        return True
    usuario.ativo = False
    usuario.dta_ativacao_conta = None
    await session.flush()
    logger.info("Deactivated account for %s", usuario.email)
    return True


async def invalidate_sessions(usuario: ArenaUser, session: AsyncSession) -> bool:
    """Invalidate all existing JWT sessions by incrementing ``session_version``.

    Any JWT token carrying the previous ``session_version`` value will be
    rejected on next validation.

    Args:
        usuario: Arena user whose sessions should be invalidated.
        session: Active async database session.

    Returns:
        bool: Always ``True``.
    """
    usuario.session_version = (usuario.session_version + 1) % 65536
    await session.flush()
    logger.info("Invalidated sessions for %s (session_version=%d)", usuario.email, usuario.session_version)
    return True


async def marcar_para_trocar_senha(usuario: ArenaUser, session: AsyncSession) -> bool:
    """Flag the user to change their password on next login.

    Args:
        usuario: Arena user to flag.
        session: Active async database session.

    Returns:
        bool: Always ``True``.
    """
    usuario.precisa_trocar_senha = True
    usuario.dta_marcacao_troca_senha = _utcnow()
    await session.flush()
    logger.info("Flagged %s for mandatory password change", usuario.email)
    return True


async def aceitar_termos_privacidade(usuario: ArenaUser, session: AsyncSession) -> bool:
    """Mark the user as having accepted the Terms of Service and Privacy Policy.

    Args:
        usuario: ArenaUser ORM object to update.
        session: Active async database session (caller owns the commit).

    Returns:
        bool: ``True`` on success, ``False`` if a database error occurs.
    """
    try:
        usuario.aceitou_termos_privacidade = True
        usuario.dta_aceitacao_termos_privacidade = datetime.now(UTC)
        session.add(usuario)
        await session.flush()
        logger.info("ToS/PP accepted for user %s", usuario.id)
        return True
    except SQLAlchemyError:
        logger.exception("Failed to update ToS/PP acceptance for user %s", usuario.id)
        return False


# ---------------------------------------------------------------------------
# Pure predicates (no I/O)
# ---------------------------------------------------------------------------


def conta_ativa(usuario: ArenaUser) -> bool:
    """Return ``True`` when the account is active and the email is confirmed.

    Args:
        usuario: Arena user to check.

    Returns:
        bool: ``True`` if the user may log in.
    """
    return usuario.ativo and usuario.email_confirmado and usuario.consentimento_responsavel


def verificar_idade_senha(usuario: ArenaUser) -> int | None:
    """Return the age of the user's password in days.

    Args:
        usuario: Arena user to check.

    Returns:
        int | None: Days since the last password change, or ``None`` if
            no change timestamp is recorded.
    """
    if not isinstance(usuario.dta_ultima_alteracao_senha, datetime):
        return None
    delta = _utcnow() - usuario.dta_ultima_alteracao_senha.replace(tzinfo=UTC)
    return delta.days
