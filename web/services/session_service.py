#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from typing import cast

import jwt
from fastapi import HTTPException, Request
from fastapi_flash import FlashCategory, FlashService
from jwtservice.core import TokenVerificationResult
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.routing import NoMatchFound

from shared.enumerations import ALL_CONTEST_ROLES, RoleEnum
from web.services.contest_service import get_contest_by_id
from web.services.htmx_redirect_service import build_auth_redirect_exception

SESSION_EXPIRED_MESSAGE = "Your session has expired. Please sign in again."


def get_validated_auth_token(request: Request) -> TokenVerificationResult | None:
    """Return the validated request token, applying any configured session cap."""
    cached = getattr(request.state, "validated_token", None)
    if cached is None:
        token = request.cookies.get("noca_access_token")
        if not token:
            return None
        auth_service = request.app.state.auth_service
        cached = auth_service.jwt_service.validate(token)
        request.state.validated_token = cached
        request.state.token_cap_exceeded = auth_service.is_absolute_session_cap_exceeded(cached)
        if not hasattr(request.state, "allow_token_refresh"):
            request.state.allow_token_refresh = False

    result = cast(TokenVerificationResult, cached)
    if not result.valid:
        return None
    if getattr(request.state, "token_cap_exceeded", False):
        return None
    return result


def mark_auth_refresh_eligible(request: Request) -> None:
    """Allow the middleware to rotate the auth cookie for this request."""
    request.state.allow_token_refresh = True


def _get_cached_auth_validation(request: Request) -> TokenVerificationResult | None:
    """Return middleware-cached JWT validation result, when available."""
    cached = getattr(request.state, "validated_token", None)
    if cached is None:
        return None
    return cast(TokenVerificationResult, cached)


def _decode_unverified_auth_cookie(request: Request) -> dict[str, object]:
    """Decode the auth cookie without using its claims for authorization."""
    token = request.cookies.get("noca_access_token")
    if not token:
        return {}
    try:
        payload = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iat": False,
                "verify_aud": False,
                "verify_iss": False,
            },
        )
    except jwt.InvalidTokenError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return cast(dict[str, object], payload)


async def _resolve_expired_session_redirect(
    request: Request,
    session: AsyncSession,
    fallback_location: str,
) -> str:
    """Return the best login URL for an expired auth cookie."""
    payload = _decode_unverified_auth_cookie(request)
    audience = payload.get("aud")
    if audience == RoleEnum.UBERADMIN.value:
        try:
            return request.url_for("login_get").path
        except NoMatchFound:
            return "/login"

    if audience not in [role.value for role in ALL_CONTEST_ROLES]:
        return fallback_location

    extra_data = payload.get("extra_data")
    if not isinstance(extra_data, dict):
        return fallback_location

    contest_id = extra_data.get("contest_id")
    if not isinstance(contest_id, str) or not contest_id:
        return fallback_location

    contest = await get_contest_by_id(session, contest_id)
    if contest is None or not contest.active:
        return fallback_location

    try:
        return request.url_for("contest_login_get", slug=contest.login_slug).path
    except NoMatchFound:
        return fallback_location


async def build_session_auth_redirect_exception(
    request: Request,
    session: AsyncSession,
    fallback_location: str,
) -> HTTPException:
    """Build an auth redirect, flashing once when the cookie JWT has expired."""
    validation = _get_cached_auth_validation(request)
    if validation is None or validation.valid or validation.status != "expired":
        return build_auth_redirect_exception(request, fallback_location)

    FlashService(request).flash(SESSION_EXPIRED_MESSAGE, FlashCategory.WARNING)
    location = await _resolve_expired_session_redirect(request, session, fallback_location)
    return build_auth_redirect_exception(request, location)


async def build_logout_redirect_url(request: Request, session: AsyncSession) -> str:
    """Build the post-logout redirect URL for the current authenticated actor.

    Args:
        request: Incoming FastAPI request used to inspect the auth cookie and
            generate route URLs with `url_for`.
        session: Database session used to resolve the contest referenced by a
            contest-scoped token.

    Returns:
        A URL string pointing to the generic login page for unauthenticated or
        UberAdmin contexts, or to the contest login page for contest-scoped users.

    Notes:
        The returned URL always includes a `msg` query parameter informing the
        user that logout succeeded. Invalid, missing, or unresolvable tokens
        fall back to the generic login route.
    """
    redirect_url = str(request.url_for("login_get"))
    result = get_validated_auth_token(request)
    if result is None:
        return redirect_url

    if result.aud == RoleEnum.UBERADMIN.value:
        return redirect_url

    if result.aud not in [role.value for role in ALL_CONTEST_ROLES]:
        return redirect_url

    contest_id = (result.extra_data or {}).get("contest_id")
    if not contest_id:
        return redirect_url

    contest = await get_contest_by_id(session, contest_id)
    if contest:
        return str(request.url_for("contest_login_get", slug=contest.login_slug))
    return redirect_url
