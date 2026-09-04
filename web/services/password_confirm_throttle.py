#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Throttled password reconfirmation for the sensitive Web actions.

Seven routes ask an already-authenticated actor to type their password again
before something irreversible or sensitive -- changing the password, starting
or ending a contest early, removing a contest, exporting password hashes, and
lifting a sign-in lockout of a login or an address (``/uberadmin/lockouts``).
Each of the original five used to verify the password per call with no failure
lockout, so a hijacked session could brute-force the password online through
any of them, and the contest removal and export additionally trigger the
heaviest operations in the module on success.

This module puts all seven behind **one** budget in the shared
:mod:`shared.services.auth_rate_limit` primitive (module ``web``, action
``password-confirm``), keyed by the actor's type-prefixed id and by the client
IP with the same ``NOCA_AUTH_RATE_LIMIT_*`` caps as login. One bucket means
rotating routes cannot multiply the allowance. The lockout is checked *before*
the hash, so a locked actor is refused even with the correct password and no
protected work starts; every wrong password and every lockout is recorded in
``security_events`` so admins can see them at ``/uberadmin/security-events``.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.auth_rate_limit import (
    AuthRateLimitSettings,
    InMemoryAuthRateLimiter,
    build_auth_throttle_identity,
    check_auth_throttle,
    record_auth_failure,
    reset_auth_throttle,
)
from shared.services.security_events import record_request_security_event
from web.config import settings
from web.models.users import UberAdmin, User
from web.services.password_service import password_matches

__all__ = [
    "PASSWORD_CONFIRM_ACTION",
    "PASSWORD_CONFIRM_LIMITER",
    "PasswordConfirmResult",
    "auth_rate_limit_settings",
    "confirm_password",
    "render_lockout",
    "retry_after_message",
]

PASSWORD_CONFIRM_ACTION = "password-confirm"
PASSWORD_CONFIRM_LIMITER = InMemoryAuthRateLimiter()
"""Process-local fallback used when Valkey cannot answer; tests reset it."""

LOCKOUT_TEMPLATE = "errors/too_many_attempts.html"


@dataclass(frozen=True, slots=True)
class PasswordConfirmResult:
    """What one reconfirmation attempt established.

    Attributes:
        ok: The password matched and the actor's budget was reset.
        locked: The attempt was refused before the hash was checked.
        retry_after_seconds: Seconds until the lockout lifts (``0`` unless locked).
    """

    ok: bool
    locked: bool
    retry_after_seconds: int = 0


def auth_rate_limit_settings() -> AuthRateLimitSettings:
    """Build the auth-throttle settings from Web config (shared with ``/login``)."""
    return AuthRateLimitSettings(
        enabled=settings.AUTH_RATE_LIMIT_ENABLED,
        window_seconds=settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
        ip_max_failures=settings.AUTH_RATE_LIMIT_IP_MAX_FAILURES,
        account_max_failures=settings.AUTH_RATE_LIMIT_ACCOUNT_MAX_FAILURES,
        lockout_seconds=settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS,
        secret=settings.JWT_SECRET_KEY,
    )


def _actor_identifier(actor: UberAdmin | User) -> str:
    """Type-prefixed stable id, so the two id spaces can never share a bucket."""
    prefix = "uberadmin" if isinstance(actor, UberAdmin) else "user"
    return f"{prefix}:{actor.id}"


def retry_after_message(retry_after_seconds: int) -> str:
    """The minute-rounded wording ``/login`` uses for a lockout."""
    minutes = max(1, (retry_after_seconds + 59) // 60)
    unit = "minute" if minutes == 1 else "minutes"
    return f"Too many failed attempts. Try again in {minutes} {unit}."


async def confirm_password(
    request: Request,
    session: AsyncSession,
    *,
    actor: UberAdmin | User,
    password: str,
    action: str,
) -> PasswordConfirmResult:
    """Verify ``password`` for ``actor`` under the shared reconfirmation budget.

    **This helper owns the transaction on ``session`` for its own writes.** On
    a mismatch or a lockout it inserts the security-event row and **commits**,
    so the record survives whatever the route does next -- including raising.
    Call it *before* staging any protected change on the same session: anything
    pending at that point is committed along with the event. The current
    callers all reconfirm first and mutate only on ``ok=True``; a new caller must
    keep that order or pass a dedicated session. On a match nothing is written
    and nothing is committed.

    Args:
        request: Current request (client IP and Valkey runtime).
        session: Session the security-event rows are written and committed on.
        actor: The authenticated actor reconfirming their password.
        password: The submitted password.
        action: Route label recorded in the event metadata (``"start_now"`` …).

    Returns:
        ``locked=True`` when the budget is spent -- decided before the hash is
        checked; ``ok=True`` when the password matched (and the budget was reset);
        otherwise a plain mismatch, which was counted and recorded.
    """
    throttle_settings = auth_rate_limit_settings()
    identity = build_auth_throttle_identity(
        request,
        module="web",
        action=PASSWORD_CONFIRM_ACTION,
        identifier=_actor_identifier(actor),
        settings=throttle_settings,
    )
    metadata_base = {"action": "password_confirm", "route": action}

    check = await check_auth_throttle(
        request, identity, settings=throttle_settings, fallback_limiter=PASSWORD_CONFIRM_LIMITER
    )
    if not check.allowed:
        retry_after = check.retry_after_seconds or settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS
        await record_request_security_event(
            session,
            request,
            module="web",
            event_type="auth_throttle_lockout",
            severity="warning",
            actor_user_id=actor.id,
            actor_label=actor.username,
            identifier_hash=identity.identifier_hash,
            metadata={**metadata_base, "reason": check.reason},
        )
        await session.commit()
        return PasswordConfirmResult(ok=False, locked=True, retry_after_seconds=retry_after)

    if password_matches(actor, password):
        await reset_auth_throttle(request, identity, fallback_limiter=PASSWORD_CONFIRM_LIMITER)
        return PasswordConfirmResult(ok=True, locked=False)

    failure = await record_auth_failure(
        request, identity, settings=throttle_settings, fallback_limiter=PASSWORD_CONFIRM_LIMITER
    )
    await record_request_security_event(
        session,
        request,
        module="web",
        event_type="auth_failure",
        severity="warning" if failure.locked else "info",
        actor_user_id=actor.id,
        actor_label=actor.username,
        identifier_hash=identity.identifier_hash,
        metadata={**metadata_base, "reason": failure.reason},
    )
    await session.commit()
    return PasswordConfirmResult(ok=False, locked=False)


def render_lockout(request: Request, *, retry_after_seconds: int, back_url: str, back_label: str) -> HTMLResponse:
    """Render the shared ``429`` page for a locked reconfirmation.

    Args:
        request: Current request.
        retry_after_seconds: Seconds until the lockout lifts (``Retry-After``).
        back_url: Where the actor should go back to.
        back_label: Label of that link.

    Returns:
        The ``429`` HTML response with a ``Retry-After`` header.
    """
    templates = request.app.state.templates
    response: HTMLResponse = templates.TemplateResponse(
        request,
        LOCKOUT_TEMPLATE,
        {
            "message": retry_after_message(retry_after_seconds),
            "back_url": back_url,
            "back_label": back_label,
        },
        status_code=429,
        headers={"Retry-After": str(retry_after_seconds)},
    )
    return response
