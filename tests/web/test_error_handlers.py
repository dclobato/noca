#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Request
from fastapi.responses import HTMLResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

import web.main as main_module
from web.error_handlers import (
    database_exception_handler,
    http_exception_response,
    unexpected_exception_handler,
)


class _FakeTemplates:
    def __init__(self) -> None:
        self.context: dict[str, Any] = {}

    def TemplateResponse(
        self,
        request: Request,
        name: str,
        context: dict[str, Any],
        *,
        status_code: int,
    ) -> HTMLResponse:
        self.context = context
        return HTMLResponse(
            f"{name}: {context['heading']} {context.get('primary_label', '')}",
            status_code=status_code,
        )


def _request(*, accept: str, templates: _FakeTemplates | None = None) -> Request:
    app = SimpleNamespace(state=SimpleNamespace(templates=templates))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/contests",
            "raw_path": b"/contests",
            "query_string": b"page=2",
            "headers": [(b"accept", accept.encode())],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "app": app,
        }
    )


def _http_exception_app() -> main_module.FastAPI:
    app = main_module.FastAPI()
    app.state.templates = _FakeTemplates()
    app.add_exception_handler(StarletteHTTPException, http_exception_response)

    @app.get("/contests", name="contests_list")
    async def contests_list() -> HTMLResponse:
        return HTMLResponse("contests")

    return app


def test_web_registers_backend_exception_handlers() -> None:
    assert main_module.app.exception_handlers[SQLAlchemyError] is database_exception_handler
    assert main_module.app.exception_handlers[ConnectionError] is database_exception_handler
    assert main_module.app.exception_handlers[TimeoutError] is database_exception_handler
    assert main_module.app.exception_handlers[Exception] is unexpected_exception_handler
    assert main_module.app.exception_handlers[StarletteHTTPException] is http_exception_response


@pytest.mark.asyncio
async def test_web_database_failure_returns_non_leaking_json() -> None:
    response = await database_exception_handler(
        _request(accept="application/json"),
        RuntimeError("postgres password is secret"),
    )

    assert response.status_code == 503
    assert json.loads(response.body) == {"detail": "Service temporarily unavailable."}
    assert b"secret" not in response.body


@pytest.mark.asyncio
async def test_web_unexpected_failure_renders_branded_html() -> None:
    templates = _FakeTemplates()

    response = await unexpected_exception_handler(
        _request(accept="text/html", templates=templates),
        RuntimeError("private failure detail"),
    )

    assert response.status_code == 500
    assert b"errors/backend.html" in response.body
    assert templates.context["status_code"] == 500
    assert templates.context["retry_url"] == "/contests?page=2"
    assert b"private failure detail" not in response.body


@pytest.mark.asyncio
async def test_web_connection_refusal_is_handled_without_asgi_reraise() -> None:
    app = main_module.FastAPI()
    app.add_exception_handler(ConnectionError, database_exception_handler)

    @app.get("/failure")
    async def failure() -> None:
        raise ConnectionRefusedError(111, "database unavailable")

    transport = ASGITransport(app=app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/failure", headers={"accept": "application/json"})

    assert response.status_code == 503
    assert response.json() == {"detail": "Service temporarily unavailable."}


@pytest.mark.asyncio
async def test_web_browser_route_not_found_renders_branded_html() -> None:
    transport = ASGITransport(app=_http_exception_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/missing-page", headers={"accept": "text/html"})

    assert response.status_code == 404
    assert "Page not found" in response.text
    assert "View contests" in response.text


@pytest.mark.asyncio
async def test_web_api_route_not_found_keeps_default_json() -> None:
    transport = ASGITransport(app=_http_exception_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/missing-page", headers={"accept": "application/json"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
