#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Throttle helpers for the anonymous endpoints that redeem an emailed token.

``/auth/activate`` *performs its action* on the ``GET``, so unlike the
password-reset link there is no ``POST`` to defer the verdict to: the check
cannot be removed, only counted. ``POST /auth/parental-consent`` is the other
consumer -- there the mutation *did* move behind a confirmation page, and the
whole redemption contract moved with it: the consent ``GET`` only renders and
neither counts a failure nor consults the lockout, because a review page must
stay free of side effects for the mail scanners and prefetchers that follow
links. Both redeeming endpoints share one ``token_redeem`` bucket on the
``shared.services.auth_rate_limit`` model, with three deliberate departures
from the password buckets:

* **Only a failed redemption counts.** A working link clicked twice -- or
  prefetched by a mail client -- costs nothing.
* **The lock refuses only bad tokens.** A password lock refuses even the right
  password, because the lock is what stops guessing. A redemption token is a
  256-bit HMAC, so the lock never stopped guessing; its job is to bound the
  oracle. Refusing a genuine link on a locked bucket would only hand anyone who
  knows an address a way to hold that account's activation hostage.
* **A success never clears the IP counter.** The account identifier is the
  token's ``sub`` claim -- read *unverified*, so tampering one user's link
  accumulates against that user instead of minting a fresh bucket per
  variation -- and falls back to the token itself when there is no payload to
  read. Either way the identifier is chosen by the caller, so the IP bucket is
  the only cap on guessing, and a success the caller can produce at will (their
  own valid link) must not wipe it.
"""

from __future__ import annotations

import base64
import binascii
import json

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.routes.auth_common import AUTH_RATE_LIMITER, _auth_rate_limit_settings, _token_failure_message
from arena.routes.auth_throttle import throttled_response
from arena.services.user_service import UserOperationStatus
from shared.services.auth_rate_limit import (
    AuthFailureRecord,
    AuthThrottleIdentity,
    build_auth_throttle_identity,
    check_auth_throttle,
    record_auth_failure,
    reset_auth_throttle,
)
from shared.services.security_events import record_request_security_event

TOKEN_REDEEM_ACTION = "token_redeem"


def token_redeem_identifier(token: str) -> str:
    """Return the throttle identifier for a redemption token.

    The JWT payload is decoded **without** verifying the signature: a
    tampered or expired link still names the account it was minted for, and
    that is what a run of failures should accumulate against. The value is
    namespaced so a ``sub`` can never collide with a raw token, and the
    caller hashes it before it becomes a key.

    Args:
        token: The raw token the link carries.

    Returns:
        ``sub:<claim>`` when the payload decodes to a non-empty string ``sub``,
        otherwise ``token:<token>``.
    """
    parts = token.split(".")
    if len(parts) == 3:
        payload_b64 = parts[1]
        try:
            padded = payload_b64 + "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded))
        except binascii.Error, UnicodeDecodeError, ValueError:
            payload = None
        if isinstance(payload, dict):
            sub = payload.get("sub")
            if isinstance(sub, str) and sub.strip():
                return f"sub:{sub}"
    return f"token:{token}"


def build_token_redeem_identity(request: Request, token: str) -> AuthThrottleIdentity:
    """Build the throttle identity for one redemption attempt.

    Args:
        request: Incoming request (supplies the client IP).
        token: The raw token the link carries.

    Returns:
        AuthThrottleIdentity: Keys for the ``token_redeem`` bucket.
    """
    return build_auth_throttle_identity(
        request,
        module="arena",
        action=TOKEN_REDEEM_ACTION,
        identifier=token_redeem_identifier(token),
        settings=_auth_rate_limit_settings(),
    )


async def reject_token_redemption(
    request: Request,
    session: AsyncSession,
    flash: FlashDep,
    identity: AuthThrottleIdentity,
    status: UserOperationStatus,
) -> Response:
    """Answer a redemption that was refused, counting it or refusing it outright.

    Called only after the token was judged and found wanting. While the bucket
    is locked the lockout page is returned and nothing is counted; otherwise
    the failure is recorded and the caller's message is flashed.

    Args:
        request: Incoming request.
        session: Active async database session (committed).
        flash: Flash-message dependency.
        identity: Identity from :func:`build_token_redeem_identity`.
        status: The refusal, used to pick the flash message.

    Returns:
        Response: The lockout page, or a redirect to the login page.
    """
    throttle_settings = _auth_rate_limit_settings()
    check = await check_auth_throttle(request, identity, settings=throttle_settings, fallback_limiter=AUTH_RATE_LIMITER)
    if not check.allowed:
        await record_request_security_event(
            session,
            request,
            module="arena",
            event_type="auth_throttle_lockout",
            severity="warning",
            identifier_hash=identity.identifier_hash,
            metadata={"action": TOKEN_REDEEM_ACTION, "reason": check.reason},
        )
        await session.commit()
        retry_after = check.retry_after_seconds or throttle_settings.lockout_seconds
        return throttled_response(request, flash, retry_after, back_route="arena_login")

    await _record_failure(request, session, identity)
    flash(_token_failure_message(status), FlashCategory.DANGER)
    return RedirectResponse(url=request.url_for("arena_login"), status_code=303)


async def _record_failure(request: Request, session: AsyncSession, identity: AuthThrottleIdentity) -> AuthFailureRecord:
    """Count one rejected redemption and persist the matching security event.

    There is no verified account to name: a rejected token may decode to
    nothing at all, which is why the event carries only the identifier hash.
    """
    failure = await record_auth_failure(
        request, identity, settings=_auth_rate_limit_settings(), fallback_limiter=AUTH_RATE_LIMITER
    )
    await record_request_security_event(
        session,
        request,
        module="arena",
        event_type="auth_failure",
        severity="warning" if failure.locked else "info",
        identifier_hash=identity.identifier_hash,
        metadata={"action": TOKEN_REDEEM_ACTION, "reason": failure.reason},
    )
    await session.commit()
    return failure


async def reset_token_redeem_throttle(request: Request, identity: AuthThrottleIdentity) -> None:
    """Clear this account's redemption failures after a link worked.

    The per-IP counters are left alone on purpose; see the module docstring.

    Args:
        request: Incoming request.
        identity: Identity from :func:`build_token_redeem_identity`.
    """
    await reset_auth_throttle(request, identity, fallback_limiter=AUTH_RATE_LIMITER, include_ip=False)
