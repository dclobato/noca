#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import logging
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from shared.services.auth_rate_limit import (
    AuthRateLimitSettings,
    InMemoryAuthRateLimiter,
    build_auth_throttle_identity,
    check_auth_throttle,
    record_auth_failure,
    reset_auth_throttle,
)
from shared.services.network_utils import NetworkService
from shared.services.security_events import record_request_security_event
from web.config import settings
from web.models.users import UberAdmin, User
from web.services.contest_service import get_contest_by_slug
from web.services.lockout_admin_service import contest_login_identifier
from web.services.password_confirm_throttle import auth_rate_limit_settings
from web.services.session_service import build_logout_redirect_url, safe_contest_next_url

logger = logging.getLogger(__name__)

router = APIRouter()
_auth_limiter = InMemoryAuthRateLimiter()


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[no-any-return]


def _safe_uberadmin_next_url(next_url: str | None) -> str:
    """Return a same-origin login destination or the UberAdmin dashboard."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//") and "\\" not in next_url:
        return next_url
    return "/uberadmin"


async def _actor_from_token(request: Request, session: AsyncSession, token: str) -> tuple[str | None, str | None]:
    """Resolve a Web access token to the local actor id and login when possible.

    Returns:
        A ``(actor_id, actor_label)`` pair. ``actor_label`` is the token subject
        (the username used to log in), snapshotted for the security-event log.
        Both are ``None`` when the token cannot be resolved to a local user.
    """
    auth_service = request.app.state.auth_service
    try:
        result = auth_service.jwt_service.validate(token)
    except Exception:
        return None, None
    if not result.valid:
        return None, None
    if result.aud == RoleEnum.UBERADMIN.value:
        actor = (
            await session.execute(select(UberAdmin.id).where(UberAdmin.username == result.sub))
        ).scalar_one_or_none()
        return (str(actor), result.sub) if actor is not None else (None, None)

    contest_id = (result.extra_data or {}).get("contest_id")
    if not isinstance(contest_id, str):
        return None, None
    actor = (
        await session.execute(select(User.id).where(User.username == result.sub, User.contest_id == contest_id))
    ).scalar_one_or_none()
    return (str(actor), result.sub) if actor is not None else (None, None)


@router.get("/login", response_class=HTMLResponse)
async def login_get(
    request: Request,
    next_url: str = "/uberadmin",
) -> HTMLResponse:
    next_url = _safe_uberadmin_next_url(next_url)
    return _templates(request).TemplateResponse(
        request,
        "auth/uberadmin_login.html",
        {
            "next_url": next_url,
            "identifier": "",
            "login_error": None,
            "identifier_invalid": False,
            "password_invalid": False,
        },
    )


@router.post("/login", response_model=None)
async def login_post(
    request: Request,
    identifier: str = Form(""),
    password: str = Form(""),
    next_url: str = Form("/uberadmin"),
) -> HTMLResponse | RedirectResponse:
    identifier = identifier.strip()
    next_url = _safe_uberadmin_next_url(next_url)
    if not identifier or not password:
        if not identifier and not password:
            login_error = "Enter your username and password."
        elif not identifier:
            login_error = "Enter your username."
        else:
            login_error = "Enter your password."
        return _templates(request).TemplateResponse(
            request,
            "auth/uberadmin_login.html",
            {
                "next_url": next_url,
                "identifier": identifier,
                "login_error": login_error,
                "identifier_invalid": not identifier,
                "password_invalid": not password,
            },
        )

    auth_service = request.app.state.auth_service
    async with request.app.state.db_session() as session:
        throttle_settings = _rate_limit_settings()
        throttle_identity = build_auth_throttle_identity(
            request,
            module="web",
            action="login",
            identifier=identifier,
            settings=throttle_settings,
        )
        throttle_check = await check_auth_throttle(
            request,
            throttle_identity,
            settings=throttle_settings,
            fallback_limiter=_auth_limiter,
        )
        if not throttle_check.allowed:
            await record_request_security_event(
                session,
                request,
                module="web",
                event_type="auth_throttle_lockout",
                severity="warning",
                identifier_hash=throttle_identity.identifier_hash,
                metadata={"action": "login", "reason": throttle_check.reason},
            )
            await session.commit()
            retry_after_seconds = throttle_check.retry_after_seconds or settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS
            retry_after_minutes = max(1, (retry_after_seconds + 59) // 60)
            retry_unit = "minute" if retry_after_minutes == 1 else "minutes"
            return _templates(request).TemplateResponse(
                request,
                "auth/uberadmin_login.html",
                {
                    "next_url": next_url,
                    "identifier": identifier,
                    "login_error": (f"Too many failed attempts. Try again in {retry_after_minutes} {retry_unit}."),
                    "identifier_invalid": False,
                    "password_invalid": False,
                },
                status_code=429,
                headers={"Retry-After": str(retry_after_seconds)},
            )
        try:
            ip_address = NetworkService.get_ip_from_request(request)
            source_port = NetworkService.get_trusted_source_port_from_request(request)
            token = await auth_service.uberadmin_login(
                identifier,
                password,
                session,
                ip_address=ip_address,
                source_port=source_port,
                user_agent=request.headers.get("User-Agent"),
            )
        except ValueError:
            logger.warning("Failed uberadmin login attempt for identifier '%s' from IP %s", identifier, ip_address)
            failure = await record_auth_failure(
                request,
                throttle_identity,
                settings=throttle_settings,
                fallback_limiter=_auth_limiter,
            )
            await record_request_security_event(
                session,
                request,
                module="web",
                event_type="auth_failure",
                severity="warning" if failure.locked else "info",
                identifier_hash=throttle_identity.identifier_hash,
                metadata={"action": "login", "reason": failure.reason},
            )
            await session.commit()
            return _templates(request).TemplateResponse(
                request,
                "auth/uberadmin_login.html",
                {
                    "next_url": next_url,
                    "identifier": identifier,
                    "login_error": "The username or password is incorrect.",
                    "identifier_invalid": True,
                    "password_invalid": True,
                },
            )
        await reset_auth_throttle(
            request,
            throttle_identity,
            fallback_limiter=_auth_limiter,
        )
        actor_user_id, actor_label = await _actor_from_token(request, session, token)
        await record_request_security_event(
            session,
            request,
            module="web",
            event_type="auth_success",
            actor_user_id=actor_user_id,
            actor_label=actor_label,
            identifier_hash=throttle_identity.identifier_hash,
            metadata={"action": "login", "scope": "uberadmin"},
        )
        await session.commit()

    response = RedirectResponse(url=next_url or "/uberadmin", status_code=303)
    response.set_cookie(
        "noca_access_token",
        token,
        httponly=True,
        samesite="lax",
        secure=settings.COOKIE_SECURE,
    )
    logger.debug("Successful login for identifier '%s' from IP %s", identifier, ip_address)
    return response


@router.get("/c/{slug}/login", response_class=HTMLResponse)
async def contest_login_get(request: Request, slug: str, next: str | None = None) -> HTMLResponse:  # noqa: A002
    """Render the contest login form, carrying a safe same-contest return page."""
    async with request.app.state.db_session() as session:
        contest = await get_contest_by_slug(slug=slug, session=session)
    return _templates(request).TemplateResponse(
        request,
        "auth/contest_login.html",
        {"contest": contest, "next_url": safe_contest_next_url(slug, next, default="") or ""},
    )


@router.post("/c/{slug}/login", response_model=None)
async def contest_login_post(
    request: Request,
    slug: str,
    flash: FlashDep,
    identifier: str = Form(""),
    password: str = Form(""),
    next_url: str = Form(""),
) -> HTMLResponse | RedirectResponse:
    auth_service = request.app.state.auth_service
    # Re-validated here rather than trusted from the form: the hidden field is
    # as forgeable as the query it came from.
    safe_next = safe_contest_next_url(slug, next_url, default=None)
    async with request.app.state.db_session() as session:
        contest = await get_contest_by_slug(slug=slug, session=session)
        throttle_settings = _rate_limit_settings()
        # Contest-scoped, not the bare name: a contest login is unique only per
        # contest, so a bare-name bucket is shared by every contest carrying it --
        # five failures against the least important contest on the deployment would
        # lock that name out of the one that is running. The contest *id* and not
        # the slug, because a slug can be renamed while a lock is live.
        throttle_identity = build_auth_throttle_identity(
            request,
            module="web",
            action="contest-login",
            identifier=contest_login_identifier(contest.id, identifier),
            settings=throttle_settings,
        )
        throttle_check = await check_auth_throttle(
            request,
            throttle_identity,
            settings=throttle_settings,
            fallback_limiter=_auth_limiter,
        )
        if not throttle_check.allowed:
            await record_request_security_event(
                session,
                request,
                module="web",
                event_type="auth_throttle_lockout",
                severity="warning",
                identifier_hash=throttle_identity.identifier_hash,
                metadata={"action": "contest-login", "contest_slug": slug, "reason": throttle_check.reason},
            )
            await session.commit()
            flash("Too many failed attempts. Try again later.", FlashCategory.DANGER)
            return _templates(request).TemplateResponse(
                request,
                "auth/contest_login.html",
                {"contest": contest, "next_url": safe_next or ""},
                status_code=429,
                headers={
                    "Retry-After": str(throttle_check.retry_after_seconds or settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS)
                },
            )
        try:
            ip_address = NetworkService.get_ip_from_request(request)
            source_port = NetworkService.get_trusted_source_port_from_request(request)
            token = await auth_service.user_login(
                identifier,
                password,
                contest.id,
                session,
                ip_address=ip_address,
                source_port=source_port,
                user_agent=request.headers.get("User-Agent"),
            )
        except ValueError:
            logger.warning("Failed login attempt for identifier '%s'@'%s' from IP %s", identifier, slug, ip_address)
            failure = await record_auth_failure(
                request,
                throttle_identity,
                settings=throttle_settings,
                fallback_limiter=_auth_limiter,
            )
            await record_request_security_event(
                session,
                request,
                module="web",
                event_type="auth_failure",
                severity="warning" if failure.locked else "info",
                identifier_hash=throttle_identity.identifier_hash,
                metadata={"action": "contest-login", "contest_slug": slug, "reason": failure.reason},
            )
            await session.commit()
            flash("Invalid username or password.", FlashCategory.DANGER)
            login_url = str(request.url_for("contest_login_get", slug=slug))
            if safe_next:
                login_url = f"{login_url}?next={quote(safe_next, safe='/?=&%')}"
            return RedirectResponse(url=login_url, status_code=303)
        await reset_auth_throttle(
            request,
            throttle_identity,
            fallback_limiter=_auth_limiter,
        )
        actor_user_id, actor_label = await _actor_from_token(request, session, token)
        await record_request_security_event(
            session,
            request,
            module="web",
            event_type="auth_success",
            actor_user_id=actor_user_id,
            actor_label=actor_label,
            identifier_hash=throttle_identity.identifier_hash,
            metadata={"action": "contest-login", "contest_slug": slug},
        )
        await session.commit()

    response = RedirectResponse(url=safe_next or f"/c/{slug}", status_code=303)
    response.set_cookie(
        "noca_access_token",
        token,
        httponly=True,
        samesite="lax",
        secure=settings.COOKIE_SECURE,
    )
    logger.debug("Successful login for identifier '%s' from IP %s", identifier, ip_address)
    return response


def _rate_limit_settings() -> AuthRateLimitSettings:
    """Build auth-throttle settings from Web config (one builder, shared with reconfirmation)."""
    return auth_rate_limit_settings()


@router.post("/logout")
async def logout(request: Request, flash: FlashDep) -> RedirectResponse:
    """Log the current user out.

    POST rather than GET: the control sits a few pixels from the profile link in
    the navbar, and a GET logout is also prefetchable by browsers and extensions,
    so an ordinary link hover could end a five-hour authenticated session.
    """
    token = request.cookies.get("noca_access_token")
    async with request.app.state.db_session() as session:
        redirect_url = await build_logout_redirect_url(request, session)
        actor_user_id, actor_label = await _actor_from_token(request, session, token) if token else (None, None)
        if token:
            request.app.state.auth_service.logout(token)
        await record_request_security_event(
            session,
            request,
            module="web",
            event_type="auth_logout",
            actor_user_id=actor_user_id,
            actor_label=actor_label,
            metadata={"token_present": bool(token), "token_valid": actor_user_id is not None},
        )
        await session.commit()

    flash("You have been logged out.", FlashCategory.INFO)
    response = RedirectResponse(url=redirect_url, status_code=303)
    response.delete_cookie("noca_access_token", httponly=True, samesite="lax")
    if settings.COOKIE_SECURE:
        response.delete_cookie("noca_access_token", httponly=True, samesite="lax", secure=True)
    return response
