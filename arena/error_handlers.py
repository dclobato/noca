#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Centralized Arena HTTP and backend exception responses."""

from __future__ import annotations

import logging
from typing import cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi_flash import FlashCategory
from starlette.exceptions import HTTPException as StarletteHTTPException

from arena.dependencies.auth import ForceLogoutException
from arena.services.session_service import (
    build_current_next_url,
    build_login_redirect_response,
    write_flash_message,
)
from shared.error_handlers import (
    BackendErrorConfig,
    create_backend_error_handlers,
    register_backend_error_handlers,
    request_accepts_html,
)

logger = logging.getLogger(__name__)


def _arena_backend_context(request: Request) -> dict[str, object]:
    """Return Arena template context with its backend-error illustration."""
    return {
        "error_image_url": request.url_for("arena_static_img", path="500.png"),
    }


_backend_config = BackendErrorConfig(
    logger=logger,
    templates_state_attr="arena_templates",
    template_name="errors/backend.html",
    unavailable_heading="Arena is temporarily unavailable",
    context_builder=_arena_backend_context,
)
_backend_handlers = create_backend_error_handlers(_backend_config)
database_exception_handler = _backend_handlers.database
unexpected_exception_handler = _backend_handlers.unexpected


def register_error_handlers(app: FastAPI) -> None:
    """Register all Arena HTTP and backend exception handlers."""
    register_backend_error_handlers(app, _backend_handlers)
    app.add_exception_handler(ForceLogoutException, force_logout_handler)
    app.add_exception_handler(StarletteHTTPException, arena_starlette_404_handler)
    app.add_exception_handler(HTTPException, arena_http_exception_handler)


def _render_404(request: Request) -> Response:
    """Render the branded Arena page-not-found response."""
    img_url = request.url_for("arena_static_img", path="404.png")
    templates: Jinja2Templates = request.app.state.arena_templates
    return cast(
        Response,
        templates.TemplateResponse(
            request,
            "errors/404.html",
            {"img404_url": img_url},
            status_code=404,
        ),
    )


async def force_logout_handler(request: Request, exc: Exception) -> RedirectResponse:
    """Redirect an invalidated Arena session to login and clear its cookie."""
    _ = cast(ForceLogoutException, exc)
    response = RedirectResponse(url=str(request.url_for("arena_login")), status_code=303)
    response.delete_cookie("arena_access_token", httponly=True, samesite="lax")
    return response


async def arena_starlette_404_handler(request: Request, exc: Exception) -> Response:
    """Render route-not-found errors for browsers and preserve API responses."""
    http_exception = cast(StarletteHTTPException, exc)
    if request_accepts_html(request) and http_exception.status_code == 404:
        return _render_404(request)
    return await http_exception_handler(request, http_exception)


async def arena_http_exception_handler(request: Request, exc: Exception) -> Response:
    """Redirect browser auth failures and render browser 404 responses."""
    http_exception = cast(HTTPException, exc)
    is_htmx_request = request.headers.get("HX-Request", "").lower() == "true"
    accepts_html = request_accepts_html(request)
    if (not accepts_html and not is_htmx_request) or http_exception.status_code not in {
        401,
        403,
        404,
    }:
        return await http_exception_handler(request, http_exception)

    if http_exception.status_code == 404:
        if not accepts_html:
            return await http_exception_handler(request, http_exception)
        return _render_404(request)

    if http_exception.status_code == 401:
        write_flash_message(request, "Please log in to continue.", FlashCategory.WARNING)
        login_redirect = build_login_redirect_response(
            request,
            next_url=build_current_next_url(request),
        )
        if is_htmx_request:
            response = Response(
                status_code=401,
                headers={"HX-Redirect": login_redirect.headers["location"]},
            )
        else:
            response = login_redirect
        response.delete_cookie("arena_access_token", httponly=True, samesite="lax")
        return response

    write_flash_message(
        request,
        "You do not have permission to access that page.",
        FlashCategory.WARNING,
    )
    if is_htmx_request:
        return Response(
            status_code=403,
            headers={"HX-Redirect": str(request.url_for("arena_dashboard"))},
        )
    return RedirectResponse(url=str(request.url_for("arena_dashboard")), status_code=303)
