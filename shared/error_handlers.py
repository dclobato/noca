#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared backend-failure responses for NOCA HTTP applications."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
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
    """Build an HTML or JSON error response using application-owned presentation."""
    if not request_accepts_html(request):
        return JSONResponse({"detail": detail}, status_code=status_code)

    request.scope.setdefault("session", {})
    templates = cast(Any, getattr(request.app.state, config.templates_state_attr))
    template_context: dict[str, object] = {
        "status_code": status_code,
        "heading": heading,
        "message": message,
        "retry_url": _current_relative_url(request),
    }
    if config.context_builder is not None:
        template_context.update(config.context_builder(request))
    if context is not None:
        template_context.update(context)

    return cast(
        Response,
        templates.TemplateResponse(
            request,
            config.template_name,
            template_context,
            status_code=status_code,
        ),
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
        """Render a generic response for an unexpected server failure."""
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
