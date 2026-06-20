#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena password reset and basic profile update service.

All database operations accept an ``AsyncSession`` so the caller controls
the transaction boundary.
"""

import logging
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_users import ArenaUser
from arena.services.token_service import ArenaTokenAction, JWTService
from arena.services.user_service import UserOperationStatus, UserServiceResult
from shared.services.email_service import EmailService
from shared.services.email_validation import EmailValidationService

logger = logging.getLogger(__name__)

_RESET_PASSWORD_TIMEOUT = 3_600  # 1 hour in seconds
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


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def solicitar_reset_senha(
    email: str,
    session: AsyncSession,
    jwt_service: JWTService,
    email_service: EmailService,
    url_base: str,
) -> UserServiceResult:
    """Send a password-reset link to the user's email address.

    Always returns ``SUCCESS`` — even for unknown addresses — to prevent
    user enumeration through timing differences.

    Args:
        email: Email address of the account requesting a reset.
        session: Active async database session.
        jwt_service: Arena JWT service for reset-token creation.
        email_service: Email delivery service.
        url_base: Base URL used to build the reset link.

    Returns:
        UserServiceResult: Always ``SUCCESS`` from the caller's perspective.
    """
    try:
        normalizado = EmailValidationService.normalize(email)
    except ValueError:
        return UserServiceResult(status=UserOperationStatus.SUCCESS)

    result = await session.execute(select(ArenaUser).where(ArenaUser.email_normalizado == normalizado))
    usuario = result.scalar_one_or_none()
    if usuario is None:
        logger.warning("Password reset requested for unknown address: %s", normalizado)
        return UserServiceResult(status=UserOperationStatus.SUCCESS)

    token = jwt_service.criar(
        action=ArenaTokenAction.RESET_PASSWORD,
        sub=usuario.email_normalizado,
        expires_in=_RESET_PASSWORD_TIMEOUT,
    )
    url = f"{url_base.rstrip('/')}/auth/password-reset?token={token}"
    body = _render_email_template("reset_password.jinja2", nome=usuario.nome, url=url)
    email_service.send_email(
        to_email=usuario.email_normalizado,
        to_name=usuario.nome,
        subject="Reset your Arena password",
        text_body=body,
    )
    logger.info("Password reset email sent to %s", normalizado)
    return UserServiceResult(status=UserOperationStatus.SUCCESS, user=usuario)


async def redefinir_senha_por_token(
    token: str,
    nova_senha: str,
    session: AsyncSession,
    jwt_service: JWTService,
) -> UserServiceResult:
    """Reset a user's password using the JWT from the reset link.

    Args:
        token: RESET_PASSWORD JWT from the email link.
        nova_senha: Plaintext replacement password (hashed by the ORM setter).
        session: Active async database session.
        jwt_service: Arena JWT service for token validation.

    Returns:
        UserServiceResult: ``SUCCESS`` with the user, or an error status.
    """
    dados = jwt_service.validar(token)
    if not dados.valid:
        if dados.reason == "expired":
            return UserServiceResult(status=UserOperationStatus.TOKEN_EXPIRED, error_message="Token expired")
        return UserServiceResult(
            status=UserOperationStatus.INVALID_TOKEN,
            error_message=f"Invalid token: {dados.reason}",
        )
    if dados.action != ArenaTokenAction.RESET_PASSWORD:
        return UserServiceResult(status=UserOperationStatus.INVALID_TOKEN, error_message="Wrong token action")
    if not dados.sub:
        return UserServiceResult(status=UserOperationStatus.INVALID_TOKEN, error_message="Missing token subject")

    result = await session.execute(select(ArenaUser).where(ArenaUser.email_normalizado == dados.sub))
    usuario = result.scalar_one_or_none()
    if usuario is None:
        return UserServiceResult(status=UserOperationStatus.USER_NOT_FOUND, error_message="User not found")

    try:
        usuario.password = nova_senha
        await session.flush()
        logger.info("Password reset for %s", usuario.email)
        return UserServiceResult(status=UserOperationStatus.SUCCESS, user=usuario)
    except SQLAlchemyError as exc:
        logger.error("Database error resetting password for %s: %s", usuario.email, exc)
        return UserServiceResult(status=UserOperationStatus.DATABASE_ERROR, error_message=str(exc))


async def atualizar_perfil(
    usuario: ArenaUser,
    session: AsyncSession,
    novo_nome: str | None = None,
    nova_dta_nascimento: date | None = None,
) -> UserServiceResult:
    """Update basic profile fields for a user.

    Only ``nome`` and ``dta_nascimento`` are handled here.  Photo and email
    changes require dedicated service calls.

    Args:
        usuario: Arena user whose profile is being updated.
        session: Active async database session.
        novo_nome: Replacement display name (skipped when ``None`` or blank).
        nova_dta_nascimento: Replacement date of birth (skipped when ``None``).

    Returns:
        UserServiceResult: ``SUCCESS`` with the updated user, or an error.
    """
    try:
        if novo_nome and novo_nome.strip():
            usuario.nome = novo_nome.strip()
        if nova_dta_nascimento is not None:
            usuario.dta_nascimento = nova_dta_nascimento
        await session.flush()
        logger.info("Profile updated for %s", usuario.email)
        return UserServiceResult(status=UserOperationStatus.SUCCESS, user=usuario)
    except SQLAlchemyError as exc:
        logger.error("Database error updating profile for %s: %s", usuario.email, exc)
        return UserServiceResult(status=UserOperationStatus.DATABASE_ERROR, error_message=str(exc))
