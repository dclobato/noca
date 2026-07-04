#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for shared backend-failure response handling."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import SQLAlchemyError

from shared.error_handlers import (
    BackendErrorConfig,
    BackendErrorHandlers,
    create_backend_error_handlers,
    register_backend_error_handlers,
)


class _FakeTemplates:
    """Capture template rendering context for assertions."""

    def __init__(self) -> None:
        self.context: dict[str, Any] = {}
        self.env = self

    def get_template(self, name: str) -> _FakeTemplate:
        """Return a fake template that stores render context for assertions."""
        return _FakeTemplate(name, self)

    def TemplateResponse(
        self,
        request: Request,
        name: str,
        context: dict[str, Any],
        *,
        status_code: int,
    ) -> HTMLResponse:
        """Return a minimal response while recording the template context."""
        self.context = context
        return HTMLResponse(name, status_code=status_code)


class _FakeTemplate:
    """Minimal Jinja2 template stand-in used by _FakeTemplates.get_template."""

    def __init__(self, name: str, store: _FakeTemplates) -> None:
        self.name = name
        self._store = store

    def render(self, context: dict[str, Any]) -> str:
        self._store.context = context
        return f"{self.name}: {context.get('heading', '')} {context.get('primary_label', '')}"


def _request(*, accept: str, templates: _FakeTemplates | None = None) -> Request:
    """Build a request with configurable content negotiation and templates."""
    app = SimpleNamespace(state=SimpleNamespace(templates=templates))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/items",
            "raw_path": b"/items",
            "query_string": b"page=2",
            "headers": [(b"accept", accept.encode())],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "app": app,
        }
    )


def _handlers() -> tuple[BackendErrorConfig, BackendErrorHandlers]:
    """Create shared handlers with application-owned presentation context."""
    config = BackendErrorConfig(
        logger=logging.getLogger(__name__),
        templates_state_attr="templates",
        template_name="errors/backend.html",
        unavailable_heading="Temporarily unavailable",
        context_builder=lambda _request: {"presentation": "application-owned"},
    )
    return config, create_backend_error_handlers(config)


@pytest.mark.asyncio
async def test_database_handler_uses_application_owned_html_context() -> None:
    """HTML backend errors include retry and application presentation context."""
    _, handlers = _handlers()
    templates = _FakeTemplates()

    response = await handlers.database(
        _request(accept="text/html", templates=templates),
        ConnectionError("database unavailable"),
    )

    assert response.status_code == 503
    assert templates.context["heading"] == "Temporarily unavailable"
    assert templates.context["retry_url"] == "/items?page=2"
    assert templates.context["presentation"] == "application-owned"


@pytest.mark.asyncio
async def test_unexpected_handler_returns_non_leaking_json() -> None:
    """JSON backend errors do not expose exception details."""
    _, handlers = _handlers()

    response = await handlers.unexpected(
        _request(accept="application/json"),
        RuntimeError("private failure detail"),
    )

    assert response.status_code == 500
    assert json.loads(response.body) == {"detail": "Internal server error."}
    assert b"private failure detail" not in response.body


def test_register_backend_error_handlers_maps_common_failures() -> None:
    """Common backend exception classes map to the configured handlers."""
    _, handlers = _handlers()
    app = FastAPI()

    register_backend_error_handlers(app, handlers)

    assert app.exception_handlers[SQLAlchemyError] is handlers.database
    assert app.exception_handlers[ConnectionError] is handlers.database
    assert app.exception_handlers[TimeoutError] is handlers.database
    assert app.exception_handlers[Exception] is handlers.unexpected
