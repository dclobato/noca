#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Helpers for Arena login-session redirects and token rotation.

Every authenticated Arena session slides: a LOGIN token is rotated once the
remaining lifetime reaches half of ``NOCA_JWT_EXPIRE_SECONDS``, so an active
user is never logged out mid-task. ``NOCA_JWT_REFRESH_MAX_SESSION_SECONDS``
optionally caps the total session length (``0``, the default, disables it).

"Remember me" is orthogonal and governs cookie *persistence* only: it sets a
``max_age`` on the cookie so the session survives a browser restart, where a
plain login uses a browser-session cookie.
"""

from __future__ import annotations

import time

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi_flash import FlashCategory

from arena.config import settings
from arena.services.token_service import ArenaTokenAction, JWTService, TokenVerificationResult
from shared.session_keepalive import refresh_window_seconds

#: ``max_age`` for the "remember me" cookie. This governs cookie *persistence*
#: only -- how long the browser keeps sending the cookie across restarts -- and
#: is no longer a session lifetime. The absolute session cap is
#: ``NOCA_JWT_REFRESH_MAX_SESSION_SECONDS`` and applies to every session.
ARENA_REMEMBER_ME_MAX_AGE = 30 * 24 * 3600
SESSION_STARTED_AT_CLAIM = "session_started_at"
# Must match fastapi_flash/service.py:_SESSION_KEY.
_SESSION_FLASH_KEY = "_flash_messages"


def write_flash_message(request: Request, message: str, category: str = FlashCategory.INFO) -> None:
    """Write a flash message directly into the Starlette session.

    Args:
        request: Current HTTP request.
        message: Human-readable message text.
        category: Flash category string.
    """
    messages: list[dict[str, str]] = request.session.get(_SESSION_FLASH_KEY, [])
    messages.append({"message": message, "category": category})
    request.session[_SESSION_FLASH_KEY] = messages


def build_current_next_url(request: Request) -> str:
    """Return the current request target as a path plus query string.

    Args:
        request: Current HTTP request.

    Returns:
        str: Same-origin redirect target built from the request path and query.
    """
    path = request.url.path
    query = request.url.query
    return f"{path}?{query}" if query else path


def safe_next_url(next_url: str | None, request: Request) -> str:
    """Return a safe same-origin next URL, or the Arena dashboard URL.

    Args:
        next_url: Candidate next URL from a query string, form field, or token.
        request: Current HTTP request.

    Returns:
        str: A same-origin path or the Arena dashboard URL.
    """
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return str(request.url_for("arena_dashboard"))


def build_login_redirect_response(
    request: Request,
    *,
    next_url: str | None = None,
    status_code: int = 303,
) -> RedirectResponse:
    """Build a redirect to the Arena login page with an optional safe next URL.

    Args:
        request: Current HTTP request.
        next_url: Optional candidate next URL. Unsafe values are omitted.
        status_code: HTTP redirect status code.

    Returns:
        RedirectResponse: Redirect to the login page.
    """
    login_url = request.url_for("arena_login")
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        login_url = login_url.include_query_params(next=next_url)
    return RedirectResponse(url=str(login_url), status_code=status_code)


def _now_epoch_seconds() -> int:
    """Return the current Unix timestamp in whole seconds."""
    return int(time.time())


def build_login_token_extra_data(
    *,
    tid: str,
    remember_me: bool,
    session_started_at: int | None = None,
) -> dict[str, object]:
    """Build the Arena LOGIN token extra-data payload."""
    return {
        "tid": tid,
        "remember_me": remember_me,
        SESSION_STARTED_AT_CLAIM: (session_started_at if session_started_at is not None else _now_epoch_seconds()),
    }


def is_remembered_login(validation: TokenVerificationResult | None) -> bool:
    """Return whether the validated Arena LOGIN token belongs to a remembered session."""
    extra_data = getattr(validation, "extra_data", None)
    return isinstance(extra_data, dict) and bool(extra_data.get("remember_me", False))


def get_session_started_at(validation: TokenVerificationResult | None) -> int | None:
    """Return the session start timestamp stamped into the token when present."""
    extra_data = getattr(validation, "extra_data", None)
    if not isinstance(extra_data, dict):
        return None
    started_at = extra_data.get(SESSION_STARTED_AT_CLAIM)
    return started_at if isinstance(started_at, int) else None


def should_refresh_login_token(validation: TokenVerificationResult | None) -> bool:
    """Return whether a valid Arena LOGIN token is inside the half-life refresh window."""
    if validation is None or not validation.valid or validation.action != ArenaTokenAction.LOGIN:
        return False
    expires_in = validation.expires_in
    if expires_in is None:
        return False
    return bool(expires_in <= refresh_window_seconds(settings.JWT_EXPIRE_SECONDS))


def is_absolute_session_cap_exceeded(validation: TokenVerificationResult | None) -> bool:
    """Return whether the optional absolute session cap has been exceeded.

    The cap is ``NOCA_JWT_REFRESH_MAX_SESSION_SECONDS`` and applies to every
    session, remembered or not. ``0`` disables it, keeping active users signed
    in for as long as they keep making requests.
    """
    max_session_seconds = settings.JWT_REFRESH_MAX_SESSION_SECONDS
    if max_session_seconds <= 0 or validation is None or not validation.valid:
        return False
    session_started_at = get_session_started_at(validation)
    if session_started_at is None:
        return False
    return _now_epoch_seconds() - session_started_at >= max_session_seconds


def build_refreshed_login_token(
    *,
    jwt_service: JWTService,
    validation: TokenVerificationResult | None,
) -> str | None:
    """Return a fresh Arena LOGIN token for any active session inside the refresh window."""
    if not should_refresh_login_token(validation):
        return None
    if is_absolute_session_cap_exceeded(validation):
        return None
    if validation is None or validation.sub is None:
        return None

    extra_data = dict(validation.extra_data or {})
    if settings.JWT_REFRESH_MAX_SESSION_SECONDS > 0 and get_session_started_at(validation) is None:
        return None
    return str(
        jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=validation.sub,
            expires_in=settings.JWT_EXPIRE_SECONDS,
            extra_data=extra_data,
        )
    )


def mark_auth_refresh_eligible(request: Request) -> None:
    """Allow the middleware to rotate the Arena auth cookie for this request."""
    request.state.allow_token_refresh = True
