#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi_flash import FlashCategory, FlashDep

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
from web.services.contest_service import get_contest_by_slug
from web.services.session_service import build_logout_redirect_url

logger = logging.getLogger(__name__)

router = APIRouter()
_auth_limiter = InMemoryAuthRateLimiter()


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[no-any-return]


@router.get("/login", response_class=HTMLResponse)
async def login_get(
    request: Request,
    next_url: str = "/uberadmin",
) -> HTMLResponse:
    return _templates(request).TemplateResponse(
        request,
        "auth/uberadmin_login.html",
        {"next_url": next_url},
    )


@router.post("/login", response_model=None)
async def login_post(
    request: Request,
    flash: FlashDep,
    identifier: str = Form(...),
    password: str = Form(...),
    next_url: str = Form("/uberadmin"),
) -> HTMLResponse | RedirectResponse:
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
            flash("Too many failed attempts. Try again later.", FlashCategory.DANGER)
            return _templates(request).TemplateResponse(
                request,
                "auth/uberadmin_login.html",
                {"next_url": next_url},
                status_code=429,
                headers={
                    "Retry-After": str(throttle_check.retry_after_seconds or settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS)
                },
            )
        try:
            ip_address = NetworkService.get_ip_from_request(request)
            token = await auth_service.uberadmin_login(
                identifier,
                password,
                session,
                ip_address=ip_address,
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
            flash("Invalid username/password.", FlashCategory.DANGER)
            redirect_to = str(request.url_for("login_get").include_query_params(next_url=next_url))
            return RedirectResponse(url=redirect_to, status_code=303)
        await reset_auth_throttle(
            request,
            throttle_identity,
            fallback_limiter=_auth_limiter,
        )

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
async def contest_login_get(request: Request, slug: str) -> HTMLResponse:
    async with request.app.state.db_session() as session:
        contest = await get_contest_by_slug(slug=slug, session=session)
    return _templates(request).TemplateResponse(
        request,
        "auth/contest_login.html",
        {"contest": contest},
    )


@router.post("/c/{slug}/login", response_model=None)
async def contest_login_post(
    request: Request,
    slug: str,
    flash: FlashDep,
    identifier: str = Form(...),
    password: str = Form(...),
) -> HTMLResponse | RedirectResponse:
    auth_service = request.app.state.auth_service
    async with request.app.state.db_session() as session:
        contest = await get_contest_by_slug(slug=slug, session=session)
        throttle_settings = _rate_limit_settings()
        throttle_identity = build_auth_throttle_identity(
            request,
            module="web",
            action="contest-login",
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
                metadata={"action": "contest-login", "contest_slug": slug, "reason": throttle_check.reason},
            )
            await session.commit()
            flash("Too many failed attempts. Try again later.", FlashCategory.DANGER)
            return _templates(request).TemplateResponse(
                request,
                "auth/contest_login.html",
                {"contest": contest},
                status_code=429,
                headers={
                    "Retry-After": str(throttle_check.retry_after_seconds or settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS)
                },
            )
        try:
            ip_address = NetworkService.get_ip_from_request(request)
            token = await auth_service.user_login(
                identifier,
                password,
                contest.id,
                session,
                ip_address=ip_address,
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
            return RedirectResponse(url=str(request.url_for("contest_login_get", slug=slug)), status_code=303)
        await reset_auth_throttle(
            request,
            throttle_identity,
            fallback_limiter=_auth_limiter,
        )

    response = RedirectResponse(url=f"/c/{slug}", status_code=303)
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
    """Build auth-throttle settings from Web config."""
    return AuthRateLimitSettings(
        enabled=settings.AUTH_RATE_LIMIT_ENABLED,
        window_seconds=settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
        ip_max_failures=settings.AUTH_RATE_LIMIT_IP_MAX_FAILURES,
        account_max_failures=settings.AUTH_RATE_LIMIT_ACCOUNT_MAX_FAILURES,
        lockout_seconds=settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS,
        secret=settings.JWT_SECRET_KEY,
    )


@router.get("/logout")
async def logout(request: Request, flash: FlashDep) -> RedirectResponse:
    async with request.app.state.db_session() as session:
        redirect_url = await build_logout_redirect_url(request, session)

    token = request.cookies.get("noca_access_token")
    if token:
        request.app.state.auth_service.logout(token)

    flash("You have been logged out.", FlashCategory.INFO)
    response = RedirectResponse(url=redirect_url, status_code=303)
    response.delete_cookie("noca_access_token", httponly=True, samesite="lax")
    if settings.COOKIE_SECURE:
        response.delete_cookie("noca_access_token", httponly=True, samesite="lax", secure=True)
    return response
