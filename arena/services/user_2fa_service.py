#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""TOTP two-factor authentication service for Arena users.

Manages the full 2FA lifecycle: activation, confirmation, deactivation, and
code validation (TOTP or backup code).  All database operations accept an
``AsyncSession`` so the caller controls the transaction boundary.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

import pyotp
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_users import ArenaUser
from arena.services import backup2fa_service as _backup
from arena.services.token_service import ArenaTokenAction, JWTService

logger = logging.getLogger(__name__)

_2FA_SESSION_TIMEOUT = 90  # seconds — how long the ACTIVATING_2FA token is valid


class Autenticacao2FA(Enum):
    """Possible outcomes for 2FA operations.

    Attributes:
        ENABLED: 2FA was successfully activated.
        NOT_ENABLED: 2FA is not active for this user.
        ENABLING: The activation flow is in progress.
        ALREADY_ENABLED: 2FA is already active; no change made.
        DISABLED: 2FA was successfully deactivated.
        INVALID_CODE: The supplied TOTP or backup code is wrong.
        REUSED: The code was already used in this time window.
        TOTP: Validation succeeded via TOTP code.
        BACKUP: Validation succeeded via a backup code.
        MISSING_TOKEN: No activation session token was provided.
        INVALID_TOKEN: The activation session token is expired or malformed.
        WRONG_USER: The token belongs to a different user.
        UNKNOWN: An unexpected error occurred.
    """

    ENABLED = 0
    NOT_ENABLED = 1
    ENABLING = 2
    ALREADY_ENABLED = 3
    DISABLED = 4
    INVALID_CODE = 5
    REUSED = 6
    TOTP = 7
    BACKUP = 8
    MISSING_TOKEN = 9
    INVALID_TOKEN = 10
    WRONG_USER = 11
    UNKNOWN = 99


@dataclass
class TwoFASetupResult:
    """Result of a 2FA setup or token-validation operation.

    Attributes:
        status: Outcome of the operation.
        secret: TOTP secret (only populated during activation confirmation).
        qr_code_base64: Base64-encoded QR Code PNG (populated by the route).
        backup_codes: Plaintext backup codes shown once after activation.
        token: Short-lived JWT for the activation session.
        user_id: Subject claim from the activation token.
    """

    status: Autenticacao2FA = Autenticacao2FA.UNKNOWN
    secret: str | None = None
    qr_code_base64: str | None = None
    backup_codes: list[str] | None = None
    token: str | None = None
    user_id: str | None = None


@dataclass
class TwoFAValidationResult:
    """Result of a 2FA code validation attempt.

    Attributes:
        success: Whether the code was accepted.
        method_used: Which method succeeded (TOTP or BACKUP).
        error_message: Human-readable error when ``success`` is ``False``.
        remaining_backup_codes: Count of unused backup codes after validation.
        security_warnings: Low-backup-code alerts surfaced to the user.
    """

    success: bool
    method_used: Autenticacao2FA | None = None
    error_message: str | None = None
    remaining_backup_codes: int | None = None
    security_warnings: list[str] = field(default_factory=list)


class User2FAError(Exception):
    """Raised when a 2FA service operation fails unexpectedly."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def iniciar_ativacao_2fa(
    usuario: ArenaUser,
    session: AsyncSession,
    jwt_service: JWTService,
) -> TwoFASetupResult:
    """Begin the TOTP 2FA activation flow for a user.

    Saves a tentative TOTP secret to the database (encrypted at rest) and
    returns a short-lived ``ACTIVATING_2FA`` JWT.  The secret is stored in
    the DB — not in the token — so it never travels in plaintext.

    Args:
        usuario: The arena user starting 2FA activation.
        session: Active async database session.
        jwt_service: Arena JWT service for token creation.

    Returns:
        TwoFASetupResult: Contains ``status=ENABLING`` and the session token,
            or ``status=ALREADY_ENABLED`` if 2FA is already active.

    Raises:
        User2FAError: On database or token-creation errors.
    """
    if usuario.usa_2fa:
        return TwoFASetupResult(status=Autenticacao2FA.ALREADY_ENABLED)
    try:
        secret = pyotp.random_base32()
        usuario.otp_secret = secret
        usuario.usa_2fa = False
        await session.flush()
        token = jwt_service.criar(
            action=ArenaTokenAction.ACTIVATING_2FA,
            sub=usuario.id,
            expires_in=_2FA_SESSION_TIMEOUT,
        )
        logger.debug("Started 2FA activation for %s (secret stored in DB)", usuario.email)
        return TwoFASetupResult(status=Autenticacao2FA.ENABLING, token=token)
    except SQLAlchemyError as exc:
        raise User2FAError(f"Error starting 2FA activation: {exc}") from exc


async def confirmar_ativacao_2fa(
    usuario: ArenaUser,
    secret: str,
    codigo_confirmacao: str,
    session: AsyncSession,
    gerar_backup_codes: bool = True,
    quantidade_backup: int = 10,
) -> TwoFASetupResult:
    """Confirm and activate 2FA after the user supplies their first TOTP code.

    Args:
        usuario: Arena user completing the activation.
        secret: TOTP secret retrieved from the database (not from the token).
        codigo_confirmacao: TOTP code provided by the user to confirm setup.
        session: Active async database session.
        gerar_backup_codes: Whether to generate backup codes on activation.
        quantidade_backup: Number of backup codes to generate (1–20).

    Returns:
        TwoFASetupResult: ``ENABLED`` with backup codes, or ``INVALID_CODE``
            if the TOTP code did not verify, or ``ALREADY_ENABLED`` if
            2FA was already active.

    Raises:
        User2FAError: On database errors.
    """
    if usuario.usa_2fa:
        return TwoFASetupResult(status=Autenticacao2FA.ALREADY_ENABLED)
    totp = pyotp.TOTP(secret)
    if not totp.verify(codigo_confirmacao, valid_window=1):
        return TwoFASetupResult(status=Autenticacao2FA.INVALID_CODE)
    try:
        usuario.usa_2fa = True
        usuario.otp_secret = secret
        usuario.ultimo_otp = codigo_confirmacao
        usuario.dta_mudanca_2fa = _utcnow()
        backup_codes: list[str] | None = None
        if gerar_backup_codes:
            backup_codes = await _backup.gerar_novos_codigos(usuario, session, quantidade_backup)
            logger.debug("Generated %d backup codes for %s", quantidade_backup, usuario.email)
        logger.info("Activated 2FA for %s", usuario.email)
        return TwoFASetupResult(status=Autenticacao2FA.ENABLED, backup_codes=backup_codes)
    except SQLAlchemyError as exc:
        raise User2FAError(f"Database error activating 2FA: {exc}") from exc


async def desativar_2fa(usuario: ArenaUser, session: AsyncSession) -> TwoFASetupResult:
    """Deactivate 2FA and invalidate all backup codes for a user.

    Args:
        usuario: Arena user disabling 2FA.
        session: Active async database session.

    Returns:
        TwoFASetupResult: ``DISABLED`` on success, or ``NOT_ENABLED`` if 2FA
            was not active.

    Raises:
        User2FAError: On database errors.
    """
    if not usuario.usa_2fa:
        return TwoFASetupResult(status=Autenticacao2FA.NOT_ENABLED)
    try:
        invalidados = await _backup.invalidar_codigos(usuario, session)
        usuario.usa_2fa = False
        usuario.otp_secret = None
        usuario.ultimo_otp = None
        usuario.dta_mudanca_2fa = _utcnow()
        logger.warning("Deactivated 2FA for %s (%d backup codes invalidated)", usuario.email, invalidados)
        return TwoFASetupResult(status=Autenticacao2FA.DISABLED)
    except SQLAlchemyError as exc:
        raise User2FAError(f"Database error deactivating 2FA: {exc}") from exc


async def validar_codigo_2fa(
    usuario: ArenaUser,
    codigo: str,
    session: AsyncSession,
) -> TwoFAValidationResult:
    """Validate a TOTP code or backup code for a user.

    Tries the TOTP code first; falls back to backup codes on failure.

    Args:
        usuario: Arena user whose 2FA code is being checked.
        codigo: Code supplied by the user (TOTP or backup).
        session: Active async database session.

    Returns:
        TwoFAValidationResult: Includes method used, remaining backup count,
            and any security warnings about low backup code stock.
    """
    warnings: list[str] = []
    if not usuario.usa_2fa or not usuario.otp_secret:
        logger.warning("2FA validation attempted for user without 2FA enabled: %s", usuario.email)
        return TwoFAValidationResult(
            success=False,
            method_used=Autenticacao2FA.NOT_ENABLED,
            error_message="2FA is not enabled for this account.",
        )
    if codigo == usuario.ultimo_otp:
        logger.warning("Replayed 2FA code for %s", usuario.email)
        return TwoFAValidationResult(
            success=False,
            method_used=Autenticacao2FA.REUSED,
            error_message="This code has already been used.",
            security_warnings=["This code was used in the previous window."],
        )
    try:
        totp = pyotp.TOTP(usuario.otp_secret)
        if totp.verify(codigo, valid_window=1):
            usuario.ultimo_otp = codigo
            backup_count = await _backup.contar_tokens_disponiveis(usuario, session)
            if backup_count == 0:
                warnings.append("CRITICAL: No backup codes remaining")
            elif backup_count <= 2:
                warnings.append(f"WARNING: Only {backup_count} backup code(s) remaining")
            return TwoFAValidationResult(
                success=True,
                method_used=Autenticacao2FA.TOTP,
                remaining_backup_codes=backup_count,
                security_warnings=warnings,
            )
        consumed = await _backup.consumir_token(usuario, codigo, session)
        if consumed:
            backup_count = await _backup.contar_tokens_disponiveis(usuario, session)
            warnings.append("Backup code used")
            if backup_count == 0:
                warnings.append("CRITICAL: No backup codes remaining")
            elif backup_count <= 2:
                warnings.append(f"WARNING: Only {backup_count} backup code(s) remaining")
            return TwoFAValidationResult(
                success=True,
                method_used=Autenticacao2FA.BACKUP,
                remaining_backup_codes=backup_count,
                security_warnings=warnings,
            )
        logger.warning("Invalid 2FA code for %s", usuario.email)
        return TwoFAValidationResult(
            success=False,
            method_used=Autenticacao2FA.INVALID_CODE,
            error_message="Invalid 2FA code.",
        )
    except SQLAlchemyError as exc:
        return TwoFAValidationResult(
            success=False,
            method_used=Autenticacao2FA.UNKNOWN,
            error_message=f"Database error during 2FA validation: {exc}",
        )
    except Exception as exc:
        logger.exception("Unexpected error during 2FA validation for %s", usuario.email)
        return TwoFAValidationResult(
            success=False,
            method_used=Autenticacao2FA.UNKNOWN,
            error_message=f"Unexpected error: {exc}",
        )


def otp_secret_formatado(value: ArenaUser | str) -> str | None:
    """Return the OTP secret formatted in groups of four for display.

    Args:
        value: An ``ArenaUser`` instance or a raw TOTP secret string.

    Returns:
        str | None: Secret formatted as groups of 4 chars, or ``None``.

    Raises:
        ValueError: If ``value`` is neither an ``ArenaUser`` nor a ``str``.
    """
    secret: str | None
    if isinstance(value, str):
        secret = value
    elif isinstance(value, ArenaUser):
        secret = value.otp_secret
    else:
        raise ValueError("value must be an ArenaUser or a str")
    if not secret:
        return None
    return " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))


def validar_token_ativacao_2fa(
    usuario: ArenaUser,
    token_sessao: str,
    jwt_service: JWTService,
) -> TwoFASetupResult:
    """Validate the ACTIVATING_2FA session token and retrieve the stored secret.

    The TOTP secret is read from the DB (where it is encrypted at rest), not
    from the JWT payload.  The token only confirms that the user started the
    activation flow.

    Args:
        usuario: Authenticated arena user claiming to finish activation.
        token_sessao: Short-lived ACTIVATING_2FA JWT from the session.
        jwt_service: Arena JWT service for token validation.

    Returns:
        TwoFASetupResult: ``ENABLING`` with the secret on success, or an
            error status (``MISSING_TOKEN``, ``INVALID_TOKEN``, ``WRONG_USER``,
            ``ALREADY_ENABLED``).
    """
    if not token_sessao:
        logger.warning("2FA activation attempted without session token for %s", usuario.email)
        return TwoFASetupResult(status=Autenticacao2FA.MISSING_TOKEN)
    resultado = jwt_service.validar(token_sessao)
    if not resultado.valid:
        logger.warning("Invalid 2FA activation token for %s", usuario.email)
        return TwoFASetupResult(status=Autenticacao2FA.INVALID_TOKEN)
    if resultado.action != ArenaTokenAction.ACTIVATING_2FA:
        logger.warning("Wrong token action for 2FA activation: %s", resultado.action)
        return TwoFASetupResult(status=Autenticacao2FA.INVALID_TOKEN)
    if str(usuario.id) != str(resultado.sub):
        logger.warning("2FA activation token belongs to a different user (attempted by %s)", usuario.email)
        return TwoFASetupResult(status=Autenticacao2FA.WRONG_USER)
    if usuario.usa_2fa:
        logger.warning("2FA already active for %s; activation token reuse rejected", usuario.email)
        return TwoFASetupResult(status=Autenticacao2FA.ALREADY_ENABLED)
    if not usuario.otp_secret:
        logger.warning("No tentative OTP secret found for %s during activation", usuario.email)
        return TwoFASetupResult(status=Autenticacao2FA.INVALID_TOKEN)
    logger.debug("2FA activation token validated for %s (secret from DB)", usuario.email)
    return TwoFASetupResult(
        status=Autenticacao2FA.ENABLING,
        secret=usuario.otp_secret,
        user_id=resultado.sub,
    )
