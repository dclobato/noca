#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Helpers for Arena login-session redirects and token rotation."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi_flash import FlashCategory

from arena.config import settings
from arena.services.token_service import ArenaTokenAction, JWTService, TokenVerificationResult

if TYPE_CHECKING:
    from arena.models.arena_users import ArenaUser

ARENA_REMEMBER_ME_MAX_AGE = 30 * 24 * 3600
SESSION_STARTED_AT_CLAIM = "session_started_at"
# Must match fastapi_flash/service.py:_SESSION_KEY.
_SESSION_FLASH_KEY = "_flash_messages"

_PROFILE_COMPLETION_FIELDS = (
    ("affiliation_id", "Affiliation"),
    ("preferred_language_id", "Preferred programming language"),
    ("country_code", "Country"),
    ("prefered_language", "AI-feedback language"),
)


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


def missing_profile_fields(user: ArenaUser) -> tuple[str, ...]:
    """Return display labels for profile information the user has not supplied.

    Args:
        user: Authenticated Arena user to inspect.

    Returns:
        tuple[str, ...]: Missing field labels in completion-page display order.
    """
    missing_fields: list[str] = []
    for attribute, label in _PROFILE_COMPLETION_FIELDS:
        value = str(getattr(user, attribute, "") or "").strip()
        if not value:
            missing_fields.append(label)
    return tuple(missing_fields)


def post_login_redirect_url(user: ArenaUser, next_url: str | None, request: Request) -> str:
    """Choose the destination after an Arena login is fully completed.

    Args:
        user: Authenticated Arena user whose profile may be incomplete.
        next_url: Candidate next URL from the login flow.
        request: Current HTTP request.

    Returns:
        str: Profile completion URL when required, otherwise a safe next URL.
    """
    if missing_profile_fields(user):
        return str(request.url_for("arena_user_profile_completion"))
    return safe_next_url(next_url, request)


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
    data: dict[str, object] = {"tid": tid, "remember_me": remember_me}
    if remember_me:
        data[SESSION_STARTED_AT_CLAIM] = session_started_at if session_started_at is not None else _now_epoch_seconds()
    return data


def is_remembered_login(validation: TokenVerificationResult | None) -> bool:
    """Return whether the validated Arena LOGIN token belongs to a remembered session."""
    extra_data = getattr(validation, "extra_data", None)
    return isinstance(extra_data, dict) and bool(extra_data.get("remember_me", False))


def get_session_started_at(validation: TokenVerificationResult | None) -> int | None:
    """Return the original remembered-session start timestamp when present."""
    extra_data = getattr(validation, "extra_data", None)
    if not isinstance(extra_data, dict):
        return None
    started_at = extra_data.get(SESSION_STARTED_AT_CLAIM)
    return started_at if isinstance(started_at, int) else None


def should_refresh_login_token(validation: TokenVerificationResult | None) -> bool:
    """Return whether a remembered Arena LOGIN token is inside the refresh window."""
    if validation is None or not validation.valid or validation.action != ArenaTokenAction.LOGIN:
        return False
    if not is_remembered_login(validation):
        return False
    expires_in = validation.expires_in
    if expires_in is None:
        return False
    refresh_threshold = max(1, settings.JWT_EXPIRE_SECONDS // 2)
    return bool(expires_in <= refresh_threshold)


def is_absolute_session_cap_exceeded(validation: TokenVerificationResult | None) -> bool:
    """Return whether a remembered Arena session has exceeded its 30-day cap."""
    if validation is None or not validation.valid or not is_remembered_login(validation):
        return False
    session_started_at = get_session_started_at(validation)
    if session_started_at is None:
        return False
    return _now_epoch_seconds() - session_started_at >= ARENA_REMEMBER_ME_MAX_AGE


def build_refreshed_login_token(
    *,
    jwt_service: JWTService,
    validation: TokenVerificationResult | None,
) -> str | None:
    """Return a fresh Arena LOGIN token for a remembered active session."""
    if not should_refresh_login_token(validation):
        return None
    if is_absolute_session_cap_exceeded(validation):
        return None
    if validation is None or validation.sub is None:
        return None

    extra_data = dict(validation.extra_data or {})
    if get_session_started_at(validation) is None:
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
