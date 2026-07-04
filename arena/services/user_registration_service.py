#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
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
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_users import ArenaUser
from arena.services.token_service import ArenaTokenAction, JWTService
from arena.services.user_service import (
    UserOperationStatus,
    UserServiceResult,
    _utcnow,
)
from shared.enumerations import ArenaRole
from shared.services.email_service import EmailService
from shared.services.email_validation import EmailValidationService

logger = logging.getLogger(__name__)

_EMAIL_VALIDATION_TIMEOUT = 86_400  # 24 hours in seconds
_EMAIL_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "template" / "emails"


@lru_cache(maxsize=1)
def _email_template_environment() -> Environment:
    """Return a cached Jinja2 environment for plain-text email templates."""
    return Environment(
        loader=FileSystemLoader(str(_EMAIL_TEMPLATE_DIR)),
        autoescape=False,
        trim_blocks=False,
        lstrip_blocks=False,
        undefined=StrictUndefined,
    )


def _render_email_template(template_name: str, **context: str) -> str:
    """Render a named email template with the given context variables.

    Args:
        template_name: Filename inside ``arena/template/emails/``.
        **context: Template variables.

    Returns:
        Rendered plain-text string.
    """
    template = _email_template_environment().get_template(template_name)
    return template.render(**context).rstrip()


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


def _enviar_email_confirmacao(
    usuario: ArenaUser,
    token: str,
    email_service: EmailService,
    url_base: str,
) -> bool:
    """Send the email confirmation link to the user.

    Args:
        usuario: Recipient Arena user.
        token: Short-lived JWT for email validation.
        email_service: Configured email delivery service.
        url_base: Base URL used to build the activation link.

    Returns:
        ``True`` when the email was dispatched successfully.
    """
    url = f"{url_base.rstrip('/')}/auth/activate?token={token}"
    body = _render_email_template("confirm_your_email.jinja2", nome=usuario.nome, url=url)
    result = email_service.send_email(
        to_email=usuario.email_normalizado,
        to_name=usuario.nome,
        subject="Confirm your email to activate your account",
        text_body=body,
    )
    return result.success


def _enviar_email_consentimento_responsavel_interno(
    usuario: ArenaUser,
    token: str,
    email_service: EmailService,
    url_base: str,
) -> bool:
    """Send the parental consent link to the registered guardian email."""
    if not usuario.email_responsavel_legal:
        return False
    url = f"{url_base.rstrip('/')}/auth/parental-consent?token={token}"
    body = _render_email_template(
        "parental_consent.jinja2",
        nome=usuario.nome,
        url=url,
    )
    result = email_service.send_email(
        to_email=usuario.email_responsavel_legal,
        to_name="Parent or legal guardian",
        subject="Consent required for Noca Arena account",
        text_body=body,
    )
    return result.success


def _enviar_email_conta_criada_confirmada(
    usuario: ArenaUser,
    email_service: EmailService,
) -> bool:
    """Send a welcome email to a user whose account was created with email pre-confirmed.

    Args:
        usuario: Recipient Arena user.
        email_service: Configured email delivery service.

    Returns:
        ``True`` when the email was dispatched successfully.
    """
    body = _render_email_template("account_activated.jinja2", nome=usuario.nome)
    result = email_service.send_email(
        to_email=usuario.email_normalizado,
        to_name=usuario.nome,
        subject="Your account has been created",
        text_body=body,
    )
    return result.success


def enviar_email_conta_existente(
    email: str,
    email_service: EmailService,
    url_base: str,
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

    Returns:
        ``True`` when the email was dispatched successfully.
    """
    base = url_base.rstrip("/")
    body = _render_email_template(
        "account_already_exists.jinja2",
        login_url=f"{base}/auth/login",
        reset_url=f"{base}/auth/password-reset",
    )
    result = email_service.send_email(
        to_email=email,
        to_name="Noca Arena user",
        subject="You already have a Noca Arena account",
        text_body=body,
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
        normalizado = EmailValidationService.normalize(email)
    except ValueError:
        return UserServiceResult(
            status=UserOperationStatus.INVALID_EMAIL,
            error_message=f"Invalid email address: {email!r}",
        )
    try:
        existing = await session.execute(select(ArenaUser).where(ArenaUser.email_normalizado == normalizado))
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
        usuario = ArenaUser(
            id=str(uuid.uuid4()),
            nome=nome,
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
        session.add(usuario)
        await session.flush()
        await session.refresh(usuario)
        token = _gerar_token_confirmacao_email(usuario, jwt_service)
        email_sent = False
        if enviar_email:
            if email_confirmado:
                email_sent = _enviar_email_conta_criada_confirmada(usuario, email_service)
            else:
                email_sent = _enviar_email_confirmacao(usuario, token, email_service, url_base)
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


def enviar_email_ativacao(
    usuario: ArenaUser,
    token: str,
    email_service: EmailService,
    url_base: str,
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

    Returns:
        ``True`` when the email was dispatched successfully.
    """
    return _enviar_email_confirmacao(usuario, token, email_service, url_base)


def enviar_email_consentimento_responsavel(
    usuario: ArenaUser,
    token: str,
    email_service: EmailService,
    url_base: str,
) -> bool:
    """Send a parent/legal guardian consent link for a pending Arena user."""
    return _enviar_email_consentimento_responsavel_interno(usuario, token, email_service, url_base)
