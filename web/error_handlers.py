#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Branded Web responses for unexpected backend failures."""

from __future__ import annotations

import logging
from typing import cast
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashService
from starlette.exceptions import HTTPException as StarletteHTTPException

from shared.enumerations import ALL_CONTEST_ROLES, RoleEnum
from shared.error_handlers import (
    GENERIC_ERROR_CODES,
    BackendErrorConfig,
    create_backend_error_handlers,
    create_validation_exception_handler,
    generic_error_response,
    is_generic_http_exception,
    register_backend_error_handlers,
    render_error_response,
    request_accepts_html,
)
from shared.services.multipart_file_size import MultipartFileTooLargeError
from web.services.contest_service import get_contest_by_id
from web.services.session_service import get_validated_auth_token
from web.template_globals import ROLE_LABELS

logger = logging.getLogger(__name__)


def _web_backend_context(_request: Request) -> dict[str, object]:
    """Return text-only Web template context."""
    return {
        "primary_url": None,
        "primary_label": "Try again",
    }


_backend_config = BackendErrorConfig(
    logger=logger,
    templates_state_attr="templates",
    template_name="errors/backend.html",
    unavailable_heading="We are temporarily unavailable",
    context_builder=_web_backend_context,
)
_backend_handlers = create_backend_error_handlers(_backend_config)
database_exception_handler = _backend_handlers.database
unexpected_exception_handler = _backend_handlers.unexpected


def register_error_handlers(app: FastAPI) -> None:
    """Register all Web HTTP and backend exception handlers."""
    register_backend_error_handlers(app, _backend_handlers)
    app.add_exception_handler(StarletteHTTPException, http_exception_response)
    app.add_exception_handler(RequestValidationError, create_validation_exception_handler(_backend_config))


async def http_exception_response(request: Request, exc: Exception) -> Response:
    """Render browser-specific HTTP errors while preserving API responses."""
    http_exception = cast(StarletteHTTPException, exc)
    if http_exception.status_code == 403:
        forbidden_response = await _logged_in_forbidden_response(request)
        if forbidden_response is not None:
            return forbidden_response

    if (
        isinstance(http_exception, MultipartFileTooLargeError)
        and request_accepts_html(request)
        and "session" in request.scope
    ):
        FlashService(request).flash(str(http_exception.detail), FlashCategory.WARNING)
        return RedirectResponse(_safe_browser_return_url(request), status_code=303)

    if http_exception.status_code == 404 and request_accepts_html(request):
        return render_error_response(
            request,
            _backend_config,
            status_code=404,
            heading="Page not found",
            message="We can't find the page you are looking for.",
            detail="Not Found",
            error_code=GENERIC_ERROR_CODES[404],
            context={
                "primary_url": str(request.url_for("contests_list")),
                "primary_label": "View contests",
            },
        )

    # A router-generated 404/405 carries no application message, so its default
    # body would only name the framework.  Anything an application authored --
    # and every other status -- keeps the response it asked for.
    if is_generic_http_exception(http_exception):
        return generic_error_response(
            request,
            _backend_config,
            status_code=http_exception.status_code,
            headers=http_exception.headers,
        )

    return await http_exception_handler(request, http_exception)


async def _logged_in_forbidden_response(request: Request) -> Response | None:
    """Redirect an authenticated actor away from a forbidden Web feature."""
    result = get_validated_auth_token(request)
    if result is None:
        return None

    try:
        role = RoleEnum(result.aud)
    except ValueError:
        return None

    destination = await _forbidden_dashboard_url(request, role, result.extra_data or {})
    dashboard_name = "UberAdmin dashboard" if role == RoleEnum.UBERADMIN else "contest dashboard"
    role_label = ROLE_LABELS[role.value]
    FlashService(request).flash(
        f"Access to this feature is forbidden for your {role_label} role. "
        f"You have been returned to the {dashboard_name}.",
        FlashCategory.DANGER,
    )

    if request.headers.get("HX-Request", "").lower() == "true":
        return Response(status_code=403, headers={"HX-Redirect": destination})
    return RedirectResponse(destination, status_code=303)


async def _forbidden_dashboard_url(
    request: Request,
    role: RoleEnum,
    token_data: dict[str, object],
) -> str:
    """Return the dashboard URL appropriate for an authenticated role."""
    if role == RoleEnum.UBERADMIN:
        return str(request.url_for("uberadmin_dashboard"))

    if role not in ALL_CONTEST_ROLES:
        return str(request.url_for("contests_list"))

    contest_id = token_data.get("contest_id")
    if not isinstance(contest_id, str) or not contest_id:
        return str(request.url_for("contests_list"))

    async with request.app.state.db_session() as session:
        contest = await get_contest_by_id(session, contest_id)
    if contest is None or not contest.active:
        return str(request.url_for("contests_list"))
    return str(request.url_for("contest_dashboard", slug=contest.login_slug))


def _safe_browser_return_url(request: Request) -> str:
    """Return a same-origin Referer path or the profile fallback."""
    referer = request.headers.get("referer")
    if not referer:
        return "/profile"

    parsed = urlsplit(referer)
    if parsed.scheme not in {"", "http", "https"}:
        return "/profile"
    if parsed.netloc and parsed.netloc != request.url.netloc:
        return "/profile"
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return "/profile"

    return parsed.path + (f"?{parsed.query}" if parsed.query else "")
