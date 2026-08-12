#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

import web.main as main_module
from shared.error_handlers import create_validation_exception_handler
from web.error_handlers import (
    _backend_config,
    database_exception_handler,
    http_exception_response,
    unexpected_exception_handler,
)


class _FakeTemplates:
    def __init__(self) -> None:
        self.context: dict[str, Any] = {}
        self.env = self

    def get_template(self, name: str) -> _FakeTemplate:
        return _FakeTemplate(name, self)

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


class _FakeTemplate:
    def __init__(self, name: str, store: _FakeTemplates) -> None:
        self.name = name
        self._store = store

    def render(self, context: dict[str, Any]) -> str:
        self._store.context = context
        return f"{self.name}: {context.get('heading', '')} {context.get('primary_label', '')}"


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
    app.add_exception_handler(
        RequestValidationError,
        create_validation_exception_handler(_backend_config),
    )

    @app.get("/contests", name="contests_list")
    async def contests_list() -> HTMLResponse:
        return HTMLResponse("contests")

    # GET-only, so a POST exercises the router's own 405.
    @app.get("/boom-http")
    async def boom_http() -> HTMLResponse:
        return HTMLResponse("ok")

    # A typed query parameter, so a non-integer exercises Pydantic's 422.
    @app.get("/needs-int")
    async def needs_int(value: int) -> HTMLResponse:
        return HTMLResponse(str(value))

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
async def test_web_api_route_not_found_uses_neutral_body() -> None:
    """A router 404 must not answer with the framework's own body shape."""
    transport = ASGITransport(app=_http_exception_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/missing-page", headers={"accept": "application/json"})

    assert response.status_code == 404
    assert response.json() == {"error": "not_found"}
    assert "detail" not in response.json()


@pytest.mark.asyncio
async def test_web_api_method_not_allowed_uses_neutral_body() -> None:
    """A wrong-method 405 leaks the same tell as a 404 and gets the same body."""
    transport = ASGITransport(app=_http_exception_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/boom-http", headers={"accept": "application/json"})

    assert response.status_code == 405
    assert response.json() == {"error": "method_not_allowed"}
    # Rewriting the body must not drop a protocol-required header: RFC 9110
    # makes Allow mandatory on a 405 and the router already computed it.
    assert "GET" in (response.headers.get("allow") or "")


@pytest.mark.asyncio
async def test_web_validation_error_does_not_echo_input_or_name_pydantic() -> None:
    """A 422 must not disclose the failing field, the reason, or the input."""
    transport = ASGITransport(app=_http_exception_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/needs-int?value=abc", headers={"accept": "application/json"})

    assert response.status_code == 422
    assert response.json() == {"error": "invalid_request"}
    for leak in ("int_parsing", "loc", "msg", "type", "input", "abc", "value"):
        assert leak not in response.text
