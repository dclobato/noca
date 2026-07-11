#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena authentication and flow-token service.

Handles password-based login, logout with JWT revocation, login history
recording, and the intermediate tokens required for 2FA and forced
password-change flows.

All database operations accept an ``AsyncSession`` — the caller owns the
transaction boundary.  JWT issuance for a full authenticated session is
left to the route layer; this service returns the authenticated ``ArenaUser``
so the route can call ``jwt_service.criar(ArenaTokenAction.LOGIN, ...)``.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_auth_records import ArenaLoginHistory
from arena.models.arena_users import ArenaUser
from arena.services.session_service import SESSION_STARTED_AT_CLAIM
from arena.services.token_service import ArenaTokenAction, JWTService
from arena.services.user_service import UserOperationStatus, UserServiceResult
from shared.age_check import AgeStatus, check_age
from shared.services.email_validation import EmailValidationService
from shared.services.geolocation import GeolocationDetails, GeolocationIP

logger = logging.getLogger(__name__)

_2FA_SESSION_TIMEOUT = 90  # seconds
_PASSWORD_CHANGE_TIMEOUT = 300  # seconds


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def efetuar_login(
    email: str,
    password: str,
    session: AsyncSession,
    ip_address: str | None = None,
    user_agent: str | None = None,
    mode: str = "password",
    geo_service: GeolocationIP | None = None,
) -> UserServiceResult:
    """Authenticate a user by email and password.

    Verifies credentials, checks account status, updates ``ultimo_login``,
    resolves the client IP to a geographic location (when ``geo_service`` is
    provided), and records an ``ArenaLoginHistory`` entry.  JWT issuance is
    the caller's responsibility.

    Args:
        email: User's email address (will be normalised).
        password: Plaintext password to verify.
        session: Active async database session.
        ip_address: Client IP address for login history (optional).
        user_agent: HTTP User-Agent header for login history (optional).
        mode: Authentication mode label stored in login history.
        geo_service: Optional geolocation service to resolve the client IP into
            the structured login-history geolocation columns (country, subdivision,
            district, city, EU flag, AS number).  When ``None`` or when the API key
            is not configured, those columns are left null.

    Returns:
        UserServiceResult: ``SUCCESS`` with the authenticated user, or an
            error status (``USER_NOT_FOUND``, ``USER_INACTIVE``,
            ``INVALID_CREDENTIALS``).
    """
    try:
        normalizado = EmailValidationService.normalize(email)
    except ValueError:
        return UserServiceResult(status=UserOperationStatus.INVALID_CREDENTIALS)

    result = await session.execute(select(ArenaUser).where(ArenaUser.email_normalizado == normalizado))
    usuario = result.scalar_one_or_none()
    if usuario is None:
        logger.warning("Login attempt for non-existent account: %s", normalizado)
        return UserServiceResult(status=UserOperationStatus.INVALID_CREDENTIALS)

    if not usuario.ativo or not usuario.email_confirmado:
        logger.warning("Login attempt for inactive/unconfirmed account: %s", normalizado)
        return UserServiceResult(
            status=UserOperationStatus.USER_INACTIVE,
            user=usuario,
            error_message="Account is inactive or email is not confirmed.",
        )

    if not usuario.check_password(password):
        logger.warning("Wrong password for %s", normalizado)
        return UserServiceResult(status=UserOperationStatus.INVALID_CREDENTIALS)

    if usuario.dta_nascimento is None:
        logger.warning("Login attempt for account missing date of birth: %s", normalizado)
        return UserServiceResult(
            status=UserOperationStatus.AGE_RECONFIRMATION_REQUIRED,
            user=usuario,
            error_message="Date of birth must be confirmed before login.",
        )

    age_status = check_age(usuario.dta_nascimento)
    if age_status == AgeStatus.BLOCKED:
        logger.warning("Login attempt for under-13 account: %s", normalizado)
        return UserServiceResult(
            status=UserOperationStatus.UNDERAGE_BLOCKED,
            user=usuario,
            error_message="Account is below the minimum allowed age.",
        )
    if age_status == AgeStatus.NEEDS_PARENTAL_CONSENT and not usuario.consentimento_responsavel:
        logger.warning("Login attempt for account pending parental consent: %s", normalizado)
        return UserServiceResult(
            status=UserOperationStatus.PARENTAL_CONSENT_REQUIRED,
            user=usuario,
            error_message="Parent or legal guardian consent is required.",
        )

    try:
        if not usuario.usa_2fa:
            history_result = await registrar_login_concluido(
                usuario,
                session,
                ip_address=ip_address,
                user_agent=user_agent,
                mode=mode,
                geo_service=geo_service,
            )
            if history_result.status != UserOperationStatus.SUCCESS:
                return history_result
        logger.info("Login succeeded for %s", normalizado)
        return UserServiceResult(status=UserOperationStatus.SUCCESS, user=usuario)
    except SQLAlchemyError as exc:
        logger.error("Database error during login for %s: %s", normalizado, exc)
        return UserServiceResult(status=UserOperationStatus.DATABASE_ERROR, error_message=str(exc))


async def efetuar_logout(
    token: str,
    jwt_service: JWTService,
) -> bool:
    """Revoke the current session JWT, preventing further use.

    Args:
        token: The session JWT to revoke.
        jwt_service: Arena JWT service (must have a revocation store configured).

    Returns:
        bool: ``True`` if revocation succeeded or the token was already invalid.
    """
    try:
        claims = jwt_service.validar(token)
        if claims.jti and jwt_service._revocation_store is not None:
            ttl = claims.expires_in or 3600
            jwt_service._revocation_store.revoke(claims.jti, ttl)
        logger.debug("Token revoked (jti=%s)", claims.jti)
        return True
    except Exception as exc:
        logger.error("Error revoking token: %s", exc)
        return False


def set_pending_2fa_token(
    usuario: ArenaUser,
    jwt_service: JWTService,
    remember_me: bool = False,
    next_page: str | None = None,
    session_started_at: int | None = None,
) -> str:
    """Create a PENDING_2FA token to carry the login through a 2FA gate.

    Args:
        usuario: The user who passed password verification.
        jwt_service: Arena JWT service for token creation.
        remember_me: Passed through in extra_data for the route to honour.
        next_page: Redirect target after 2FA, passed through in extra_data.
        session_started_at: Original remembered-session start timestamp.

    Returns:
        str: Signed PENDING_2FA JWT.
    """
    extra_data: dict[str, object] = {"remember_me": remember_me, "next": next_page}
    if session_started_at is not None:
        extra_data[SESSION_STARTED_AT_CLAIM] = session_started_at
    return str(
        jwt_service.criar(
            action=ArenaTokenAction.PENDING_2FA,
            sub=usuario.id,
            expires_in=_2FA_SESSION_TIMEOUT,
            extra_data=extra_data,
        )
    )


async def get_pending_2fa_token_data(
    token: str,
    session: AsyncSession,
    jwt_service: JWTService,
) -> UserServiceResult:
    """Validate a PENDING_2FA token and return the associated user.

    Args:
        token: PENDING_2FA JWT from the session.
        session: Active async database session.
        jwt_service: Arena JWT service for token validation.

    Returns:
        UserServiceResult: ``SUCCESS`` with user and extra_data, or an error.
    """
    dados = jwt_service.validar(token)
    if not dados.valid or dados.action != ArenaTokenAction.PENDING_2FA or dados.extra_data is None:
        return UserServiceResult(
            status=UserOperationStatus.INVALID_TOKEN,
            error_message=dados.reason,
        )
    if dados.sub is None:
        return UserServiceResult(status=UserOperationStatus.INVALID_CREDENTIALS)
    result = await session.execute(select(ArenaUser).where(ArenaUser.id == dados.sub))
    usuario = result.scalar_one_or_none()
    if usuario is None:
        return UserServiceResult(status=UserOperationStatus.USER_NOT_FOUND)
    return UserServiceResult(
        status=UserOperationStatus.SUCCESS,
        user=usuario,
        extra_data=dados.extra_data,
    )


def set_pending_password_change_token(
    usuario: ArenaUser,
    jwt_service: JWTService,
    remember_me: bool = False,
    next_page: str | None = None,
    session_started_at: int | None = None,
) -> str:
    """Create a PENDING_PASSWORD_CHANGE token for a forced password-change flow.

    Args:
        usuario: The user who must change their password.
        jwt_service: Arena JWT service for token creation.
        remember_me: Passed through in extra_data.
        next_page: Redirect target after the password change.
        session_started_at: Original remembered-session start timestamp.

    Returns:
        str: Signed PENDING_PASSWORD_CHANGE JWT.
    """
    extra_data: dict[str, object] = {"remember_me": remember_me, "next": next_page}
    if session_started_at is not None:
        extra_data[SESSION_STARTED_AT_CLAIM] = session_started_at
    return str(
        jwt_service.criar(
            action=ArenaTokenAction.PENDING_PASSWORD_CHANGE,
            sub=usuario.id,
            expires_in=_PASSWORD_CHANGE_TIMEOUT,
            extra_data=extra_data,
        )
    )


async def get_pending_password_change_token_data(
    token: str,
    session: AsyncSession,
    jwt_service: JWTService,
) -> UserServiceResult:
    """Validate a PENDING_PASSWORD_CHANGE token and return the associated user.

    Args:
        token: PENDING_PASSWORD_CHANGE JWT from the session.
        session: Active async database session.
        jwt_service: Arena JWT service for token validation.

    Returns:
        UserServiceResult: ``SUCCESS`` with user and extra_data, or an error.
    """
    dados = jwt_service.validar(token)
    if not dados.valid or dados.action != ArenaTokenAction.PENDING_PASSWORD_CHANGE or dados.extra_data is None:
        return UserServiceResult(
            status=UserOperationStatus.INVALID_TOKEN,
            error_message=dados.reason,
        )
    if dados.sub is None:
        return UserServiceResult(status=UserOperationStatus.INVALID_CREDENTIALS)
    result = await session.execute(select(ArenaUser).where(ArenaUser.id == dados.sub))
    usuario = result.scalar_one_or_none()
    if usuario is None:
        return UserServiceResult(status=UserOperationStatus.USER_NOT_FOUND)
    return UserServiceResult(
        status=UserOperationStatus.SUCCESS,
        user=usuario,
        extra_data=dados.extra_data,
    )


async def registrar_login_concluido(
    usuario: ArenaUser,
    session: AsyncSession,
    ip_address: str | None = None,
    user_agent: str | None = None,
    mode: str = "password",
    geo_service: GeolocationIP | None = None,
) -> UserServiceResult:
    """Record a completed Arena login in the user's audit history.

    Args:
        usuario: Authenticated Arena user.
        session: Active async database session.
        ip_address: Client IP address for login history (optional).
        user_agent: HTTP User-Agent header for login history (optional).
        mode: Authentication method label stored in login history.
        geo_service: Optional geolocation service for the client IP.

    Returns:
        UserServiceResult: ``SUCCESS`` with the user, or ``DATABASE_ERROR``.
    """
    try:
        usuario.ultimo_login = _utcnow()
        details: GeolocationDetails | None = None
        if ip_address and geo_service:
            details = geo_service.get_details_by_ip(ip_address)
        await _registrar_login(usuario, ip_address, details, user_agent, mode, session)
        return UserServiceResult(status=UserOperationStatus.SUCCESS, user=usuario)
    except SQLAlchemyError as exc:
        logger.error("Database error recording completed login for %s: %s", usuario.email_normalizado, exc)
        return UserServiceResult(status=UserOperationStatus.DATABASE_ERROR, error_message=str(exc))


async def _registrar_login(
    usuario: ArenaUser,
    ip_address: str | None,
    details: GeolocationDetails | None,
    user_agent: str | None,
    mode: str,
    session: AsyncSession,
) -> None:
    session.add(
        ArenaLoginHistory(
            arena_user_id=usuario.id,
            dta_login=_utcnow(),
            ip_address=ip_address,
            country_code=details.country_code if details else None,
            subdivision_code=details.subdivision_code if details else None,
            district=details.district if details else None,
            city=details.city if details else None,
            is_eu=details.is_eu if details else None,
            as_number=details.as_number if details else None,
            user_agent=user_agent,
            mode=mode,
        )
    )
    await session.flush()
