#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared backend-failure responses for NOCA HTTP applications."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

ExceptionHandler = Callable[[Request, Exception], Awaitable[Response]]
ErrorContextBuilder = Callable[[Request], Mapping[str, object]]


@dataclass(frozen=True)
class BackendErrorConfig:
    """Configure shared backend-error rendering for one application."""

    logger: logging.Logger
    templates_state_attr: str
    template_name: str
    unavailable_heading: str
    context_builder: ErrorContextBuilder | None = None


@dataclass(frozen=True)
class BackendErrorHandlers:
    """Hold the configured database and unexpected exception handlers."""

    database: ExceptionHandler
    unexpected: ExceptionHandler


def _is_connectivity_error(exc: OSError) -> bool:
    """Return True for OS-level network connectivity failures.

    Catches the asyncio "Multiple exceptions: Connect call failed" case that
    asyncio raises as a bare OSError (not a ConnectionError subclass) when all
    TCP addresses are refused.  Also matches direct ConnectionError subclasses
    that somehow bypass the dedicated handler.
    """
    if isinstance(exc, ConnectionError):
        return True
    msg = str(exc)
    return "Connect call failed" in msg or "Multiple exceptions" in msg


def request_accepts_html(request: Request) -> bool:
    """Return whether the client explicitly accepts an HTML response."""
    return "text/html" in request.headers.get("accept", "").lower()


def render_error_response(
    request: Request,
    config: BackendErrorConfig,
    *,
    status_code: int,
    heading: str,
    message: str,
    detail: str,
    context: Mapping[str, object] | None = None,
    error_code: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> Response:
    """Build an HTML or JSON error response using application-owned presentation.

    Renders the Jinja2 template eagerly and synchronously so that any rendering
    failure is caught here rather than propagating through the ASGI send pipeline
    (which would cause Starlette's ExceptionMiddleware to re-raise the original
    exception and produce a spurious uvicorn error log).  Falls back to a minimal
    HTML string if template rendering itself fails.

    Args:
        error_code: When set, non-HTML clients receive ``{"error": <code>}``
            instead of ``{"detail": <detail>}``.  The ``detail`` shape is the
            framework's own and names the stack to anyone probing for it, so
            generic router and validation failures use the neutral form.
        headers: Response headers carried over from the original exception.
            Rewriting the *body* must not drop protocol-required headers -- a 405
            has to keep the ``Allow`` the router computed (RFC 9110).
    """
    header_dict = dict(headers) if headers else None
    if not request_accepts_html(request):
        if error_code is not None:
            return JSONResponse({"error": error_code}, status_code=status_code, headers=header_dict)
        return JSONResponse({"detail": detail}, status_code=status_code, headers=header_dict)

    request.scope.setdefault("session", {})
    template_context: dict[str, object] = {
        "request": request,
        "status_code": status_code,
        "heading": heading,
        "message": message,
        "retry_url": _current_relative_url(request),
    }
    if config.context_builder is not None:
        with contextlib.suppress(Exception):
            template_context.update(config.context_builder(request))
    if context is not None:
        template_context.update(context)

    try:
        templates = cast(Any, getattr(request.app.state, config.templates_state_attr, None))
        html: str = templates.env.get_template(config.template_name).render(template_context)
        return HTMLResponse(html, status_code=status_code, headers=header_dict)
    except Exception:
        return HTMLResponse(
            f"<html><body><h1>{status_code}</h1><p>{message}</p></body></html>",
            status_code=status_code,
            headers=header_dict,
        )


def create_backend_error_handlers(config: BackendErrorConfig) -> BackendErrorHandlers:
    """Create backend exception handlers configured for one application."""

    async def database_exception_handler(request: Request, exc: Exception) -> Response:
        """Render a temporary-unavailability response for database failures."""
        config.logger.warning(
            "Database unavailable while handling %s %s: %s",
            request.method,
            request.url.path,
            exc,
        )
        return render_error_response(
            request,
            config,
            status_code=503,
            heading=config.unavailable_heading,
            message="A required service is not responding. Wait a moment, then try again.",
            detail="Service temporarily unavailable.",
        )

    async def unexpected_exception_handler(request: Request, exc: Exception) -> Response:
        """Render a generic response for an unexpected server failure.

        OSError with an errno that indicates a connection problem (e.g. the asyncio
        "Multiple exceptions: Connect call failed" case) is treated as a database/
        infrastructure outage rather than a programming error so it gets a 503 and a
        WARNING log instead of an ERROR with a full traceback.
        """
        if isinstance(exc, OSError) and _is_connectivity_error(exc):
            config.logger.warning(
                "Infrastructure unreachable while handling %s %s: %s",
                request.method,
                request.url.path,
                exc,
            )
            return render_error_response(
                request,
                config,
                status_code=503,
                heading=config.unavailable_heading,
                message="A required service is not responding. Wait a moment, then try again.",
                detail="Service temporarily unavailable.",
            )
        config.logger.error(
            "Unexpected failure while handling %s %s",
            request.method,
            request.url.path,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return render_error_response(
            request,
            config,
            status_code=500,
            heading="Something went wrong",
            message="We could not complete your request. Try again in a moment.",
            detail="Internal server error.",
        )

    return BackendErrorHandlers(
        database=database_exception_handler,
        unexpected=unexpected_exception_handler,
    )


def register_backend_error_handlers(
    app: FastAPI,
    handlers: BackendErrorHandlers,
) -> None:
    """Register the common backend-failure exception classes."""
    app.add_exception_handler(SQLAlchemyError, handlers.database)
    app.add_exception_handler(ConnectionError, handlers.database)
    app.add_exception_handler(TimeoutError, handlers.database)
    app.add_exception_handler(Exception, handlers.unexpected)


# Statuses the HTTP router itself produces when no application code had an
# opinion.  Deliberately excludes 401/403/409/500: those carry messages an
# application authored on purpose, and rewriting them would destroy information
# the caller needs (and, for the animator control panel, information its
# JavaScript reads back).
GENERIC_HTTP_STATUSES = frozenset({404, 405})

# Neutral, framework-agnostic body codes.  The FastAPI/Starlette default shapes
# -- ``{"detail": "Not Found"}`` and Pydantic's ``{"detail": [{"loc": ..., "msg":
# ..., "type": ..., "input": ...}]}`` -- name the stack precisely, and the
# validation one additionally discloses internal parameter names and echoes the
# caller's input back.
GENERIC_ERROR_CODES: dict[int, str] = {
    404: "not_found",
    405: "method_not_allowed",
    422: "invalid_request",
}

_GENERIC_ERROR_PRESENTATION: dict[int, tuple[str, str]] = {
    404: ("Page not found", "We can't find the page you are looking for."),
    405: ("Request not allowed", "That action is not available for this address."),
    422: ("Invalid request", "Part of that request was not valid. Check the address and try again."),
}


def is_generic_http_exception(exc: StarletteHTTPException) -> bool:
    """Return whether an HTTP exception carries no application-authored message.

    Starlette fills ``detail`` with the status phrase when a raiser passes none,
    so a detail equal to that phrase means nothing was authored.  Checking the
    status first keeps ``HTTPStatus`` from raising on a non-standard code.
    """
    if exc.status_code not in GENERIC_HTTP_STATUSES:
        return False
    return exc.detail == HTTPStatus(exc.status_code).phrase


def generic_error_response(
    request: Request,
    config: BackendErrorConfig,
    *,
    status_code: int,
    headers: Mapping[str, str] | None = None,
) -> Response:
    """Render a neutral error response for a generic router or validation failure.

    Args:
        headers: Headers from the original exception, carried through so that
            replacing the body cannot drop a protocol-required header such as the
            ``Allow`` a 405 must carry.
    """
    heading, message = _GENERIC_ERROR_PRESENTATION[status_code]
    return render_error_response(
        request,
        config,
        status_code=status_code,
        heading=heading,
        message=message,
        detail=message,
        error_code=GENERIC_ERROR_CODES[status_code],
        headers=headers,
    )


def create_validation_exception_handler(config: BackendErrorConfig) -> ExceptionHandler:
    """Create a handler that answers request-validation failures neutrally.

    The exception is never inspected: reporting which field failed and why is
    exactly the disclosure being removed.
    """

    async def validation_exception_handler(request: Request, _exc: Exception) -> Response:
        """Return a neutral 422 response."""
        return generic_error_response(request, config, status_code=422)

    return validation_exception_handler


def create_generic_http_exception_handler(config: BackendErrorConfig) -> ExceptionHandler:
    """Create a handler that neutralizes generic router errors only."""

    async def http_exception_response(request: Request, exc: Exception) -> Response:
        """Neutralize a generic 404/405 and pass anything authored straight through."""
        http_exception = cast(StarletteHTTPException, exc)
        if is_generic_http_exception(http_exception):
            return generic_error_response(
                request,
                config,
                status_code=http_exception.status_code,
                headers=http_exception.headers,
            )
        return await http_exception_handler(request, http_exception)

    return http_exception_response


def register_generic_error_handlers(app: FastAPI, config: BackendErrorConfig) -> None:
    """Register neutral router and validation handlers for an application.

    Owning the registration here keeps the ``starlette`` import in ``shared``,
    which every runtime already depends on, so a module needs no direct
    dependency on it just to name the exception class the router raises.
    """
    app.add_exception_handler(
        StarletteHTTPException,
        create_generic_http_exception_handler(config),
    )
    app.add_exception_handler(
        RequestValidationError,
        create_validation_exception_handler(config),
    )


def _current_relative_url(request: Request) -> str:
    """Return the current relative URL for the retry action."""
    retry_url = request.url.path
    if request.url.query:
        retry_url = f"{retry_url}?{request.url.query}"
    return retry_url
