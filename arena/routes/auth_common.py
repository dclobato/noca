#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared helpers for Arena authentication routes.

Not a route module — contains no router.  Import from this module in the
individual auth route files to avoid duplicating non-trivial logic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from fastapi.responses import Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.models.arena_users import ArenaUser
from arena.services.session_service import (
    ARENA_REMEMBER_ME_MAX_AGE,
    build_login_token_extra_data,
    get_session_started_at,
    is_remembered_login,
)
from arena.services.token_service import ArenaTokenAction
from arena.services.user_service import UserOperationStatus
from shared.services.auth_rate_limit import (
    AuthRateLimitSettings,
    InMemoryAuthRateLimiter,
    build_auth_throttle_identity,
    check_auth_throttle,
    record_auth_failure,
)
from shared.services.password_service import PasswordPolicy, PasswordPolicyError
from shared.services.security_events import record_request_security_event

_REMEMBER_ME_MAX_AGE = ARENA_REMEMBER_ME_MAX_AGE
AUTH_RATE_LIMITER = InMemoryAuthRateLimiter()


def _auth_rate_limit_settings() -> AuthRateLimitSettings:
    """Build Arena auth-throttle settings from config."""
    return AuthRateLimitSettings(
        enabled=settings.AUTH_RATE_LIMIT_ENABLED,
        window_seconds=settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
        ip_max_failures=settings.AUTH_RATE_LIMIT_IP_MAX_FAILURES,
        account_max_failures=settings.AUTH_RATE_LIMIT_ACCOUNT_MAX_FAILURES,
        lockout_seconds=settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS,
        secret=settings.JWT_SECRET_KEY,
    )


async def enforce_resend_throttle(
    request: Request,
    session: AsyncSession,
    *,
    action: str,
) -> int | None:
    """Rate-limit an authenticated email-resend action by client IP.

    These routes send an email on every request and are gated only by session
    state, so they need an IP-scoped abuse cap independent of any account
    identifier. The identity is built with ``identifier=None`` so only the IP
    bucket is used.

    Args:
        request: Incoming request.
        session: Active async database session (committed when a lockout is
            recorded).
        action: Stable throttle action slug for this resend flow.

    Returns:
        The retry-after seconds when the caller is currently locked out;
        otherwise records one attempt against the IP bucket and returns
        ``None``.
    """
    throttle_settings = _auth_rate_limit_settings()
    identity = build_auth_throttle_identity(
        request,
        module="arena",
        action=action,
        identifier=None,
        settings=throttle_settings,
    )
    check = await check_auth_throttle(
        request,
        identity,
        settings=throttle_settings,
        fallback_limiter=AUTH_RATE_LIMITER,
    )
    if not check.allowed:
        await record_request_security_event(
            session,
            request,
            module="arena",
            event_type="auth_throttle_lockout",
            severity="warning",
            metadata={"action": action, "reason": check.reason},
        )
        await session.commit()
        return check.retry_after_seconds or throttle_settings.lockout_seconds
    await record_auth_failure(
        request,
        identity,
        settings=throttle_settings,
        fallback_limiter=AUTH_RATE_LIMITER,
    )
    return None


def _login_token_expires_in() -> int:
    """Return the LOGIN token lifetime used for Arena session JWTs."""
    return settings.JWT_EXPIRE_SECONDS


def _login_token_extra_data(
    usuario: ArenaUser,
    *,
    remember_me: bool,
    session_started_at: int | None = None,
) -> dict[str, Any]:
    """Build the LOGIN token extra_data payload."""
    return build_login_token_extra_data(
        tid=usuario.get_token_id(),
        remember_me=remember_me,
        session_started_at=session_started_at,
    )


def _issue_login_token(
    *,
    jwt_service: Any,
    usuario: ArenaUser,
    remember_me: bool,
    session_started_at: int | None = None,
) -> str:
    """Create a signed Arena LOGIN token with remember-me-aware expiry."""
    return str(
        jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=usuario.id,
            expires_in=_login_token_expires_in(),
            extra_data=_login_token_extra_data(
                usuario,
                remember_me=remember_me,
                session_started_at=session_started_at,
            ),
        )
    )


def _set_login_cookie(response: Response, *, token: str, remember_me: bool) -> None:
    """Attach the Arena login cookie with the correct persistence flags."""
    cookie_kwargs: dict[str, Any] = {
        "key": "arena_access_token",
        "value": token,
        "httponly": True,
        "samesite": "lax",
        "secure": settings.COOKIE_SECURE,
    }
    if remember_me:
        cookie_kwargs["max_age"] = _REMEMBER_ME_MAX_AGE
    response.set_cookie(**cookie_kwargs)


def _validated_login_uses_remember_me(request: Request) -> bool:
    """Return whether the current validated Arena LOGIN token is remembered."""
    validation = getattr(request.state, "validated_token", None)
    return is_remembered_login(validation)


def _validated_login_session_started_at(request: Request) -> int | None:
    """Return the original remembered-session start timestamp from the validated LOGIN token."""
    validation = getattr(request.state, "validated_token", None)
    return get_session_started_at(validation)


def _flash_password_age_warning(usuario: Any, flash: FlashDep) -> None:
    """Flash a warning if the user's password exceeds the configured maximum age."""
    max_age = settings.ARENA_PASSWORD_MAX_AGE
    if max_age <= 0:
        return
    last_changed = usuario.dta_ultima_alteracao_senha
    if last_changed is None:
        return
    if last_changed.tzinfo is None:
        last_changed = last_changed.replace(tzinfo=UTC)
    age_days = (datetime.now(UTC) - last_changed).days
    if age_days >= max_age:
        flash(
            f"Your password is {age_days} days old. Consider updating it from your profile.",
            FlashCategory.WARNING,
        )


def _token_failure_message(status: UserOperationStatus) -> str:
    """Map token service statuses to user-facing messages."""
    if status == UserOperationStatus.TOKEN_EXPIRED:
        return "This link has expired. Please request a new one."
    if status == UserOperationStatus.USER_NOT_FOUND:
        return "We could not find the account for this link."
    if status == UserOperationStatus.EMAIL_ALREADY_CONFIRMED:
        return "Your email address is already confirmed. You can log in."
    return "This link is invalid. Please request a new one."


def _login_failure_message(status: UserOperationStatus) -> str:
    """Map login service statuses to user-facing messages."""
    if status == UserOperationStatus.DATABASE_ERROR:
        return "A server error occurred. Please try again."
    if status == UserOperationStatus.PARENTAL_CONSENT_REQUIRED:
        return "Parent or legal guardian consent is required before you can log in."
    if status == UserOperationStatus.AGE_RECONFIRMATION_REQUIRED:
        return "Confirm your date of birth before logging in."
    if status == UserOperationStatus.UNDERAGE_BLOCKED:
        return "The platform is not intended for users under 13 years old."
    return "Invalid email or password."


def _validate_password_fields(password: str, confirm_password: str) -> str | None:
    """Validate password confirmation and configured password policy."""
    if password != confirm_password:
        return "Passwords do not match."
    try:
        PasswordPolicy(settings).validate_new_password(password)
    except PasswordPolicyError as exc:
        return str(exc)
    return None
