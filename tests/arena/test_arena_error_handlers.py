#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

import arena.main as main_module
from arena.dependencies.auth import ForceLogoutException
from arena.error_handlers import (
    arena_http_exception_handler,
    arena_starlette_404_handler,
    database_exception_handler,
    force_logout_handler,
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
            f"{name}: {context.get('heading', '')} {context.get('img404_url', '')}",
            status_code=status_code,
        )


class _FakeTemplate:
    def __init__(self, name: str, store: _FakeTemplates) -> None:
        self.name = name
        self._store = store

    def render(self, context: dict[str, Any]) -> str:
        self._store.context = context
        return f"{self.name}: {context.get('heading', '')} {context.get('img404_url', '')}"


def _request(*, accept: str, templates: _FakeTemplates | None = None) -> Request:
    app = main_module.FastAPI()
    app.state.arena_templates = templates

    @app.get("/static/img/{path}", name="arena_static_img")
    async def static_image(path: str) -> HTMLResponse:
        return HTMLResponse(path)

    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/problems",
            "raw_path": b"/problems",
            "query_string": b"page=2",
            "headers": [(b"accept", accept.encode())],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "app": app,
        }
    )


def _http_exception_app() -> main_module.FastAPI:
    app = main_module.FastAPI()
    app.state.arena_templates = _FakeTemplates()
    app.add_exception_handler(StarletteHTTPException, arena_starlette_404_handler)
    app.add_exception_handler(HTTPException, arena_http_exception_handler)

    @app.get("/static/img/{path}", name="arena_static_img")
    async def static_image(path: str) -> HTMLResponse:
        return HTMLResponse(path)

    @app.get("/explicit-404")
    async def explicit_404() -> None:
        raise HTTPException(status_code=404, detail="Missing Arena page")

    return app


def test_arena_registers_backend_exception_handlers() -> None:
    assert main_module.app.exception_handlers[SQLAlchemyError] is database_exception_handler
    assert main_module.app.exception_handlers[ConnectionError] is database_exception_handler
    assert main_module.app.exception_handlers[TimeoutError] is database_exception_handler
    assert main_module.app.exception_handlers[Exception] is unexpected_exception_handler
    assert main_module.app.exception_handlers[ForceLogoutException] is force_logout_handler
    assert main_module.app.exception_handlers[StarletteHTTPException] is arena_starlette_404_handler
    assert main_module.app.exception_handlers[HTTPException] is arena_http_exception_handler


@pytest.mark.asyncio
async def test_arena_database_failure_renders_branded_html() -> None:
    templates = _FakeTemplates()

    response = await database_exception_handler(
        _request(accept="text/html", templates=templates),
        RuntimeError("postgres password is secret"),
    )

    assert response.status_code == 503
    assert b"errors/backend.html" in response.body
    assert templates.context["status_code"] == 503
    assert templates.context["retry_url"] == "/problems?page=2"
    assert str(templates.context["error_image_url"]) == "http://testserver/static/img/500.png"
    assert b"secret" not in response.body


@pytest.mark.asyncio
async def test_arena_unexpected_failure_returns_non_leaking_json() -> None:
    response = await unexpected_exception_handler(
        _request(accept="application/json"),
        RuntimeError("private failure detail"),
    )

    assert response.status_code == 500
    assert json.loads(response.body) == {"detail": "Internal server error."}
    assert b"private failure detail" not in response.body


@pytest.mark.asyncio
async def test_arena_connection_refusal_is_handled_without_asgi_reraise() -> None:
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
@pytest.mark.parametrize("path", ["/missing-page", "/explicit-404"])
async def test_arena_browser_404_renders_branded_html(path: str) -> None:
    transport = ASGITransport(app=_http_exception_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path, headers={"accept": "text/html"})

    assert response.status_code == 404
    assert "errors/404.html" in response.text
    assert "/static/img/404.png" in response.text


@pytest.mark.asyncio
async def test_arena_api_route_not_found_keeps_default_json() -> None:
    transport = ASGITransport(app=_http_exception_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/missing-page", headers={"accept": "application/json"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
