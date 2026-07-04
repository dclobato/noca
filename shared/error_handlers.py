#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared backend-failure responses for NOCA HTTP applications."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.exc import SQLAlchemyError

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
) -> Response:
    """Build an HTML or JSON error response using application-owned presentation.

    Renders the Jinja2 template eagerly and synchronously so that any rendering
    failure is caught here rather than propagating through the ASGI send pipeline
    (which would cause Starlette's ExceptionMiddleware to re-raise the original
    exception and produce a spurious uvicorn error log).  Falls back to a minimal
    HTML string if template rendering itself fails.
    """
    if not request_accepts_html(request):
        return JSONResponse({"detail": detail}, status_code=status_code)

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
        return HTMLResponse(html, status_code=status_code)
    except Exception:
        return HTMLResponse(
            f"<html><body><h1>{status_code}</h1><p>{message}</p></body></html>",
            status_code=status_code,
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


def _current_relative_url(request: Request) -> str:
    """Return the current relative URL for the retry action."""
    retry_url = request.url.path
    if request.url.query:
        retry_url = f"{retry_url}?{request.url.query}"
    return retry_url
