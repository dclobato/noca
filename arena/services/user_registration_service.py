#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena user registration and email delivery service.

Handles new user creation, email confirmation, parental consent emails, and
the public helpers for dispatching activation/consent links.

For token-based email and consent *validation*, see
:mod:`arena.services.user_email_service`. For account state mutations, see
:mod:`arena.services.user_service`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.email_templates import render_email
from arena.models.arena_users import ArenaUser
from arena.services.token_service import ArenaTokenAction, JWTService
from arena.services.user_service import (
    UserOperationStatus,
    UserServiceResult,
    _utcnow,
)
from arena.services.user_throttle_hash_service import refresh_user_throttle_hashes
from arena.services.user_visibility_service import is_shielded
from arena.services.username_service import generate_unique_username
from shared.enumerations import ArenaRole
from shared.services.email_service import EmailService
from shared.services.email_validation import EmailValidationService

logger = logging.getLogger(__name__)

_EMAIL_VALIDATION_TIMEOUT = 86_400  # 24 hours in seconds

#: How many handles to try before giving up on a new account. Each attempt
#: already asks the database for an unclaimed name; a clash here means another
#: transaction took that exact name in the window between the check and the
#: insert, which needs two independent 1-in-2.5-million coincidences to happen
#: three times running.
_USERNAME_INSERT_ATTEMPTS = 3

#: How the two backends name the violated username constraint. PostgreSQL
#: reports the constraint (`uq_arena_users_username`); SQLite names the columns
#: instead (`UNIQUE constraint failed: arena_users.username`). Both markers are
#: specific to this constraint -- a duplicate email reports
#: `uq_arena_users_email_normalizado` or `arena_users.email_normalizado` -- so
#: neither widens the retry loop to unrelated integrity errors, which would be
#: retried pointlessly and then misreported as a username problem.
_USERNAME_CONFLICT_MARKERS = ("uq_arena_users_username", "arena_users.username")


def _is_username_conflict(exc: IntegrityError) -> bool:
    """Report whether an integrity error was the username uniqueness constraint.

    Args:
        exc: The error raised by the failed insert.

    Returns:
        bool: True when the username constraint was the one violated.
    """
    message = str(exc.orig)
    return any(marker in message for marker in _USERNAME_CONFLICT_MARKERS)


async def _insert_with_unique_username(session: AsyncSession, usuario: ArenaUser) -> bool:
    """Insert a new user, redrawing the username on a uniqueness clash.

    The insert runs inside a savepoint so that a clash rolls back only the
    failed attempt. Rolling back the whole session instead would discard
    whatever the caller had already done in its own transaction.

    Args:
        session: Open Arena database session.
        usuario: The unsaved user, with every field but ``username`` set.

    Returns:
        bool: True when the row was inserted; False when every attempt clashed.

    Raises:
        IntegrityError: If the insert failed for any reason other than the
            username constraint.
    """
    for attempt in range(_USERNAME_INSERT_ATTEMPTS):
        usuario.username = await generate_unique_username(session)
        try:
            async with session.begin_nested():
                session.add(usuario)
                await session.flush()
        except IntegrityError as exc:
            if not _is_username_conflict(exc):
                raise
            logger.warning(
                "Username %r was taken between check and insert (attempt %d of %d)",
                usuario.username,
                attempt + 1,
                _USERNAME_INSERT_ATTEMPTS,
            )
            continue
        return True
    return False


def _gerar_token_confirmacao_email(usuario: ArenaUser, jwt_service: JWTService) -> str:
    """Generate a VALIDATE_EMAIL JWT for the given user."""
    return str(
        jwt_service.criar(
            action=ArenaTokenAction.VALIDATE_EMAIL,
            sub=usuario.email_normalizado,
            expires_in=_EMAIL_VALIDATION_TIMEOUT,
        )
    )


def _gerar_token_consentimento_responsavel(usuario: ArenaUser, jwt_service: JWTService) -> str:
    """Generate a PARENTAL_CONSENT JWT for the given user."""
    return str(
        jwt_service.criar(
            action=ArenaTokenAction.PARENTAL_CONSENT,
            sub=usuario.id,
            expires_in=_EMAIL_VALIDATION_TIMEOUT,
        )
    )


async def _enviar_email_confirmacao(
    usuario: ArenaUser,
    token: str,
    email_service: EmailService,
    url_base: str,
    *,
    actor_key: str,
) -> bool:
    """Send the email confirmation link to the user.

    Args:
        usuario: Recipient Arena user.
        token: Short-lived JWT for email validation.
        email_service: Configured email delivery service.
        url_base: Base URL used to build the activation link.
        actor_key: Budget identity of the requester (the client IP before login).

    Returns:
        ``True`` when the email was dispatched successfully.
    """
    url = f"{url_base.rstrip('/')}/auth/activate?token={token}"
    email_content = render_email("confirm_your_email", nome=usuario.nome, url=url)
    result = await email_service.send_email(
        to_email=usuario.email_normalizado,
        to_name=usuario.nome,
        subject=email_content.subject,
        text_body=email_content.body,
        actor_key=actor_key,
    )
    return result.success


async def _enviar_email_consentimento_responsavel_interno(
    usuario: ArenaUser,
    token: str,
    email_service: EmailService,
    url_base: str,
    *,
    actor_key: str,
) -> bool:
    """Send the parental consent link to the registered guardian email."""
    if not usuario.email_responsavel_legal:
        return False
    url = f"{url_base.rstrip('/')}/auth/parental-consent?token={token}"
    email_content = render_email(
        "parental_consent",
        nome=usuario.nome,
        url=url,
    )
    result = await email_service.send_email(
        to_email=usuario.email_responsavel_legal,
        to_name="Parent or legal guardian",
        subject=email_content.subject,
        text_body=email_content.body,
        actor_key=actor_key,
    )
    return result.success


async def _enviar_email_conta_criada_confirmada(
    usuario: ArenaUser,
    email_service: EmailService,
    *,
    actor_key: str,
) -> bool:
    """Send a welcome email to a user whose account was created with email pre-confirmed.

    Args:
        usuario: Recipient Arena user.
        email_service: Configured email delivery service.
        actor_key: Budget identity of the requester.

    Returns:
        ``True`` when the email was dispatched successfully.
    """
    email_content = render_email("account_activated", nome=usuario.nome)
    result = await email_service.send_email(
        to_email=usuario.email_normalizado,
        to_name=usuario.nome,
        subject=email_content.subject,
        text_body=email_content.body,
        actor_key=actor_key,
    )
    return result.success


async def enviar_email_conta_existente(
    email: str,
    email_service: EmailService,
    url_base: str,
    *,
    actor_key: str,
) -> bool:
    """Notify an address that a signup was attempted for an existing account.

    This keeps the sign-up flow enumeration-safe: both the fresh-signup and the
    already-registered branches return the identical neutral response, and the
    only account-specific signal (log in or reset your password) is delivered
    out-of-band to the address owner instead of to the requester.

    Args:
        email: Raw email address submitted on the sign-up form.
        email_service: Configured email delivery service.
        url_base: Base URL used to build the login and reset links.
        actor_key: Budget identity of the requester (the client IP).

    Returns:
        ``True`` when the email was dispatched successfully.
    """
    base = url_base.rstrip("/")
    email_content = render_email(
        "account_already_exists",
        login_url=f"{base}/auth/login",
        reset_url=f"{base}/auth/password-reset",
    )
    result = await email_service.send_email(
        to_email=email,
        to_name=f"{settings.BRAND_NAME} user",
        subject=email_content.subject,
        text_body=email_content.body,
        actor_key=actor_key,
    )
    return result.success


async def registrar_usuario(
    nome: str,
    email: str,
    password: str,
    session: AsyncSession,
    jwt_service: JWTService,
    email_service: EmailService,
    url_base: str,
    role: ArenaRole = ArenaRole.ARENA_USER,
    ativo: bool = True,
    email_confirmado: bool = False,
    enviar_email: bool = True,
    dta_nascimento: date | None = None,
    email_responsavel_legal: str | None = None,
    consentimento_responsavel: bool = True,
    aceitou_termos_privacidade: bool = False,
    dta_aceitacao_termos_privacidade: datetime | None = None,
) -> UserServiceResult:
    """Register a new Arena user.

    Args:
        nome: Full display name.
        email: Email address (will be normalised).
        password: Plaintext password (will be hashed by the ORM setter).
        session: Active async database session.
        jwt_service: Arena JWT service for confirmation token creation.
        email_service: Email delivery service.
        url_base: Base URL used to build the confirmation link.
        role: Arena role to assign (default ``ARENA_USER``).
        ativo: Whether the account starts active.
        email_confirmado: Skip the email confirmation step when ``True``.
        enviar_email: Send a confirmation or welcome email when ``True``.
        dta_nascimento: Optional date of birth to store.
        email_responsavel_legal: Optional parent/legal guardian email.
        consentimento_responsavel: Whether parental consent is already satisfied.
        aceitou_termos_privacidade: Whether the user accepted the Terms of Service
            and Privacy Policy.
        dta_aceitacao_termos_privacidade: Timestamp when the user accepted the
            Terms of Service and Privacy Policy.

    Returns:
        UserServiceResult: ``SUCCESS`` with the new user and confirmation token,
            or an error status.
    """
    try:
        canonical = EmailValidationService.canonicalize(email)
    except ValueError:
        return UserServiceResult(
            status=UserOperationStatus.INVALID_EMAIL,
            error_message=f"Invalid email address: {email!r}",
        )
    try:
        existing = await session.execute(select(ArenaUser).where(ArenaUser.email_canonical == canonical))
        if existing.scalar_one_or_none() is not None:
            return UserServiceResult(
                status=UserOperationStatus.USER_ALREADY_REGISTERED,
                error_message=f"{email!r} is already registered",
            )
        now = _utcnow()
        normalized_guardian_email: str | None = None
        if email_responsavel_legal:
            try:
                normalized_guardian_email = EmailValidationService.normalize(email_responsavel_legal)
            except ValueError:
                return UserServiceResult(
                    status=UserOperationStatus.INVALID_EMAIL,
                    error_message=f"Invalid parent/legal guardian email: {email_responsavel_legal!r}",
                )
        # An adult is published under the name they gave; a 13-17 year-old, or an
        # account with no recorded date of birth, under their handle. The shield
        # is re-derived on every read regardless -- this only decides the stored
        # opt-in, so that an adult is not silently pseudonymized by a default they
        # never chose, and a minor is never published by one.
        usuario = ArenaUser(
            id=str(uuid.uuid4()),
            nome=nome,
            full_name_public=not is_shielded(dta_nascimento),
            dta_nascimento=dta_nascimento,
            email_responsavel_legal=normalized_guardian_email,
            consentimento_responsavel=consentimento_responsavel,
            dta_consentimento_responsavel=now if consentimento_responsavel else None,
            aceitou_termos_privacidade=aceitou_termos_privacidade,
            dta_aceitacao_termos_privacidade=dta_aceitacao_termos_privacidade,
            role=role,
            ativo=ativo,
            dta_ativacao_conta=now if ativo else None,
            email_confirmado=email_confirmado,
            dta_validacao_email=now if email_confirmado else None,
            session_version=0,
            precisa_trocar_senha=False,
            usa_2fa=False,
            com_foto=False,
            dta_rating_update=None,
            ai_backend_credits=5,
            user_rating=0,
            solved_problems=0,
            created_at=now,
            updated_at=now,
        )
        usuario.email = email
        usuario.password = password
        if not await _insert_with_unique_username(session, usuario):
            return UserServiceResult(
                status=UserOperationStatus.USERNAME_CONFLICT,
                error_message="Could not allocate a unique username for the new account.",
            )
        await refresh_user_throttle_hashes(session, usuario)
        await session.refresh(usuario)
        token = _gerar_token_confirmacao_email(usuario, jwt_service)
        email_sent = False
        if enviar_email:
            if email_confirmado:
                email_sent = await _enviar_email_conta_criada_confirmada(
                    usuario, email_service, actor_key=f"user:{usuario.id}"
                )
            else:
                email_sent = await _enviar_email_confirmacao(
                    usuario, token, email_service, url_base, actor_key=f"user:{usuario.id}"
                )
        logger.info("Registered user %s (ativo=%s, email_confirmado=%s)", usuario.email, ativo, email_confirmado)
        return UserServiceResult(
            status=UserOperationStatus.SUCCESS,
            user=usuario,
            token=token,
            email_sent=email_sent,
        )
    except SQLAlchemyError as exc:
        logger.error("Database error registering user %s: %s", email, exc)
        return UserServiceResult(status=UserOperationStatus.DATABASE_ERROR, error_message=str(exc))


async def enviar_email_ativacao(
    usuario: ArenaUser,
    token: str,
    email_service: EmailService,
    url_base: str,
    *,
    actor_key: str,
) -> bool:
    """Send the account-activation confirmation link to the user.

    Intended to be called after the user record has been committed to the
    database, so the email is only dispatched when all other validations
    (e.g. photo upload) have already succeeded.

    Args:
        usuario: Recipient Arena user.
        token: Short-lived VALIDATE_EMAIL JWT.
        email_service: Configured email delivery service.
        url_base: Base URL used to build the activation link.
        actor_key: Budget identity of the requester.

    Returns:
        ``True`` when the email was dispatched successfully.
    """
    return await _enviar_email_confirmacao(usuario, token, email_service, url_base, actor_key=actor_key)


async def enviar_email_consentimento_responsavel(
    usuario: ArenaUser,
    token: str,
    email_service: EmailService,
    url_base: str,
    *,
    actor_key: str,
) -> bool:
    """Send a parent/legal guardian consent link for a pending Arena user."""
    return await _enviar_email_consentimento_responsavel_interno(
        usuario, token, email_service, url_base, actor_key=actor_key
    )
