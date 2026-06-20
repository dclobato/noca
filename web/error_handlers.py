#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Branded Web responses for unexpected backend failures."""

from __future__ import annotations

import logging
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from shared.error_handlers import (
    BackendErrorConfig,
    create_backend_error_handlers,
    register_backend_error_handlers,
    render_error_response,
    request_accepts_html,
)

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


async def http_exception_response(request: Request, exc: Exception) -> Response:
    """Render browser 404 errors while preserving default HTTP error responses."""
    http_exception = cast(StarletteHTTPException, exc)
    if http_exception.status_code != 404 or not request_accepts_html(request):
        return await http_exception_handler(request, http_exception)

    return render_error_response(
        request,
        _backend_config,
        status_code=404,
        heading="Page not found",
        message="We can't find the page you are looking for.",
        detail="Not Found",
        context={
            "primary_url": str(request.url_for("contests_list")),
            "primary_label": "View contests",
        },
    )
