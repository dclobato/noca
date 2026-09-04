#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
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
        USERNAME_CONFLICT: A unique username could not be allocated.
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
    USERNAME_CONFLICT = 14
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


def _clear_public_identity_flags(usuario: ArenaUser) -> None:
    """Clear both public-identity opt-ins for an account entering the age shield.

    Read-side masking alone is not enough, which is the whole reason this exists:
    a 13-17 year-old whose ``public_profile`` is merely *hidden* would have their
    profile **auto-publish** on the morning of their eighteenth birthday, when the
    mask lifts and the stored ``True`` is suddenly honoured. Turning 18 must only
    unblock the toggle, never flip it. So the stored flags are cleared, and the
    user opts back in explicitly if they still want to.

    Args:
        usuario: Arena user transitioning into the shielded band.
    """
    usuario.public_profile = False
    usuario.full_name_public = False


async def regularizar_data_nascimento(
    user_id: str,
    dta_nascimento: date,
    session: AsyncSession,
) -> UserServiceResult:
    """Store a missing date of birth and apply age-gate defaults.

    A non-``ALLOWED`` outcome clears both public-identity opt-ins, for the reason
    given in :func:`_clear_public_identity_flags`. It deliberately does **not**
    invalidate sessions, unlike :func:`update_date_of_birth`: that is a
    consent-revocation concern owned by the guardian-revocation work, and it would
    buy nothing here, since ``get_current_arena_user`` re-checks ``ativo`` and the
    consent gate on every request, so a surviving JWT already grants nothing.

    ``consent_generation`` is bumped on **both** branches because each is a
    consent-state transition -- the ``ALLOWED`` branch grants consent just as the
    other revokes it -- and the epoch's purpose is to bind a guardian's revocation
    link to exactly one such state.

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
        _clear_public_identity_flags(usuario)
    usuario.consent_generation += 1
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

    A change *into* the shielded band additionally clears both public-identity
    opt-ins; see :func:`_clear_public_identity_flags` for why masking them at read
    time would not be enough. ``consent_generation`` is bumped on every effective
    change, so a guardian revocation link minted against an earlier epoch cannot
    be replayed after the account's age standing has moved.

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
        _clear_public_identity_flags(usuario)
        await invalidate_sessions(usuario, session)

    usuario.consent_generation += 1
    await session.flush()
    logger.warning("Updated date of birth for %s (age_status=%s)", usuario.email, status.name)
    return status


async def grant_parental_consent(usuario: ArenaUser, session: AsyncSession) -> bool:
    """Record parent/legal guardian consent, bumping the consent epoch.

    The single write path for granting consent, shared by the guardian token flow and
    the admin toggle so both bump ``consent_generation``. The bump is what mints a new
    revocation epoch: the link in the confirmation email that follows a grant carries the
    post-grant value, and every link issued before it becomes inert.

    Args:
        usuario: Arena user whose guardian is granting consent.
        session: Active async database session.

    Returns:
        bool: ``True`` when this call performed the transition, ``False`` when consent
            was already granted. Callers must use this to decide whether to notify or
            record an event, so a re-opened link neither re-emails nor re-audits.
    """
    if usuario.consentimento_responsavel:
        return False
    usuario.consentimento_responsavel = True
    usuario.dta_consentimento_responsavel = _utcnow()
    usuario.consent_generation += 1
    await session.flush()
    logger.info("Parental consent granted for %s (consent_generation=%d)", usuario.email, usuario.consent_generation)
    return True


async def revoke_parental_consent(usuario: ArenaUser, session: AsyncSession) -> bool:
    """Withdraw parent/legal guardian consent and suspend the account.

    The single write path for revocation, shared by the guardian token flow and the admin
    toggle so the two cannot drift in security effect. Composed from existing primitives:
    the consent fields are cleared, the account is deactivated, live JWTs are killed by
    bumping ``session_version`` (``desativar_conta`` does not do this on its own, so
    callers pair the two), both public-identity opt-ins are cleared, and the consent epoch
    is bumped so every outstanding revocation link becomes inert.

    **Revocation suspends; it does not erase.** Submissions, verdicts, badges, ratings and
    class memberships are untouched -- erasure is a separate right with its own flow.
    Recovery is a fresh consent grant, either through
    ``POST /auth/resend-parental-consent`` or the admin toggle.

    ``ranking_visible`` is deliberately **not** touched. The public user ranking already
    drops the row through ``ativo``, while the affiliation aggregation in
    ``shared/services/arena_rating.py`` filters on ``ranking_visible`` alone -- so clearing
    it would move a third party's affiliation rating as a side effect of one family's
    consent decision.

    Args:
        usuario: Arena user whose guardian is withdrawing consent.
        session: Active async database session.

    Returns:
        bool: Always ``True``.
    """
    usuario.consentimento_responsavel = False
    usuario.dta_consentimento_responsavel = None
    await desativar_conta(usuario, session)
    await invalidate_sessions(usuario, session)
    _clear_public_identity_flags(usuario)
    usuario.consent_generation += 1
    await session.flush()
    logger.warning("Parental consent revoked for %s (consent_generation=%d)", usuario.email, usuario.consent_generation)
    return True


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
