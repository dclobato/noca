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

from shared.services.network_utils import NetworkService
from web.config import settings
from web.services.contest_service import get_contest_by_slug
from web.services.session_service import build_logout_redirect_url

logger = logging.getLogger(__name__)

router = APIRouter()


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
) -> RedirectResponse:
    auth_service = request.app.state.auth_service
    async with request.app.state.db_session() as session:
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
            flash("Invalid username/password.", FlashCategory.DANGER)
            redirect_to = str(request.url_for("login_get").include_query_params(next_url=next_url))
            return RedirectResponse(url=redirect_to, status_code=303)

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
) -> RedirectResponse:
    auth_service = request.app.state.auth_service
    async with request.app.state.db_session() as session:
        contest = await get_contest_by_slug(slug=slug, session=session)
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
            flash("Invalid username or password.", FlashCategory.DANGER)
            return RedirectResponse(url=str(request.url_for("contest_login_get", slug=slug)), status_code=303)

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
