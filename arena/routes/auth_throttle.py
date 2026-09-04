#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Throttle helpers for Arena routes that verify a secret behind a session.

The password and TOTP verification routes (``2fa/confirm``, ``2fa/disable``,
``change-password``) are online guessing oracles for anyone holding a hijacked
session or pending token. They share the ``shared.services.auth_rate_limit``
failure/lockout model that login already uses, keyed by the **user id** (so a
hijacked session cannot escape the cap by rotating IPs) plus the client IP.

These helpers own the three steps every such route repeats: the pre-check that
answers a ``429`` while locked, the failure record that may trip the lock, and
the shared lockout page.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import Request
from fastapi.responses import HTMLResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_users import ArenaUser
from arena.routes.auth_common import AUTH_RATE_LIMITER, _auth_rate_limit_settings
from shared.services.auth_rate_limit import (
    AuthFailureRecord,
    AuthThrottleIdentity,
    build_auth_throttle_identity,
    check_auth_throttle,
    record_auth_failure,
    reset_auth_throttle,
)
from shared.services.security_events import record_request_security_event

PASSWORD_VERIFY_ACTION = "password_verify"
"""Shared bucket for every route that re-verifies the account password.

``change-password`` and ``2fa/disable`` check the same secret, so alternating
between them must not double the guess budget.
"""

TOTP_CONFIRM_ACTION = "2fa_confirm"
"""Bucket for the 2FA setup confirmation, independent of the login 2FA one."""

THROTTLED_MESSAGE = "Too many failed attempts. Try again later."


async def check_verification_throttle(
    request: Request,
    session: AsyncSession,
    *,
    action: str,
    user: ArenaUser,
) -> tuple[AuthThrottleIdentity, int | None]:
    """Check whether a secret-verifying action is currently locked out.

    Args:
        request: Incoming request.
        session: Active async database session (committed when a lockout is
            recorded).
        action: Stable throttle action slug.
        user: The account whose secret the route is about to verify.

    Returns:
        The throttle identity to pass to :func:`record_verification_failure` /
        :func:`reset_verification_throttle`, and the retry-after seconds when
        the caller is locked out, otherwise ``None``.
    """
    throttle_settings = _auth_rate_limit_settings()
    identity = build_auth_throttle_identity(
        request,
        module="arena",
        action=action,
        identifier=user.id,
        settings=throttle_settings,
    )
    check = await check_auth_throttle(
        request,
        identity,
        settings=throttle_settings,
        fallback_limiter=AUTH_RATE_LIMITER,
    )
    if check.allowed:
        return identity, None
    await record_request_security_event(
        session,
        request,
        module="arena",
        event_type="auth_throttle_lockout",
        severity="warning",
        actor_user_id=user.id,
        actor_label=user.email_normalizado,
        identifier_hash=identity.identifier_hash,
        metadata={"action": action, "reason": check.reason},
    )
    await session.commit()
    return identity, check.retry_after_seconds or throttle_settings.lockout_seconds


async def record_verification_failure(
    request: Request,
    session: AsyncSession,
    identity: AuthThrottleIdentity,
    *,
    action: str,
    user: ArenaUser,
) -> AuthFailureRecord:
    """Count one wrong secret and persist the matching security event.

    Args:
        request: Incoming request.
        session: Active async database session (committed).
        identity: Identity returned by :func:`check_verification_throttle`.
        action: Stable throttle action slug.
        user: The account whose secret was guessed wrong.

    Returns:
        AuthFailureRecord: ``locked`` is ``True`` when this failure tripped the
        lockout, so the caller can void any partially guessed state.
    """
    failure = await record_auth_failure(
        request,
        identity,
        settings=_auth_rate_limit_settings(),
        fallback_limiter=AUTH_RATE_LIMITER,
    )
    await record_request_security_event(
        session,
        request,
        module="arena",
        event_type="auth_failure",
        severity="warning" if failure.locked else "info",
        actor_user_id=user.id,
        actor_label=user.email_normalizado,
        identifier_hash=identity.identifier_hash,
        metadata={"action": action, "reason": failure.reason},
    )
    await session.commit()
    return failure


async def reset_verification_throttle(request: Request, identity: AuthThrottleIdentity) -> None:
    """Clear the failure counters after the secret verified.

    Args:
        request: Incoming request.
        identity: Identity returned by :func:`check_verification_throttle`.
    """
    await reset_auth_throttle(request, identity, fallback_limiter=AUTH_RATE_LIMITER)


def throttled_response(
    request: Request,
    flash: FlashDep,
    retry_after: int,
    *,
    back_route: str,
    back_params: dict[str, Any] | None = None,
    message: str = THROTTLED_MESSAGE,
) -> Response:
    """Render the shared lockout page as a ``429`` with ``Retry-After``.

    A redirect cannot carry ``Retry-After``, and the pages these routes belong
    to need heavy context (a QR code, the whole profile), so lockouts answer
    one small dedicated page instead of re-rendering the form.

    Args:
        request: Incoming request.
        flash: Flash message dependency.
        retry_after: Seconds until the caller may try again.
        back_route: Named route the page links back to.
        back_params: Path parameters for ``back_route``, when it takes any.
        message: Flash text shown on the page.

    Returns:
        Response: HTML ``429`` response.
    """
    flash(message, FlashCategory.DANGER)
    templates: Any = request.app.state.arena_templates
    return cast(
        HTMLResponse,
        templates.TemplateResponse(
            request,
            "auth/throttled.html",
            {
                "retry_after_minutes": max(1, -(-retry_after // 60)),
                "back_url": request.url_for(back_route, **(back_params or {})),
            },
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        ),
    )
