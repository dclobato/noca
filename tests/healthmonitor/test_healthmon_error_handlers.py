#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for neutral health-monitor error responses.

The health monitor previously registered no exception handlers at all, so every
404 and 422 was the raw framework default on a public, unauthenticated page.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient

from healthmonitor.error_handlers import register_error_handlers

pytestmark = pytest.mark.asyncio

_HEALTHMON_DIR = Path(__file__).resolve().parents[2] / "healthmonitor"
_SHARED_DIR = Path(__file__).resolve().parents[2] / "shared"


def _app() -> FastAPI:
    """Build an app with the real handlers and the real template environment.

    Real templates matter: ``render_error_response`` catches any rendering
    failure and falls back to a minimal inline document, so a broken template
    would still return 404 and pass a status-only assertion.
    """
    app = FastAPI()
    templates = Jinja2Templates(directory=_HEALTHMON_DIR / "template")
    templates.env.globals["app_version"] = "test"
    templates.env.globals["brand_name"] = "NOCA Health Monitor"
    app.state.templates = templates
    register_error_handlers(app)

    app.mount(
        "/static/css",
        StaticFiles(directory=_HEALTHMON_DIR / "static" / "css"),
        name="healthmon_static_css",
    )
    app.mount(
        "/static/vendor",
        StaticFiles(directory=_SHARED_DIR / "static" / "vendor"),
        name="static_vendor",
    )

    @app.get("/", name="healthmon_dashboard")
    async def dashboard() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/needs-int")
    async def needs_int(value: int) -> dict[str, int]:
        return {"value": value}

    @app.get("/authored")
    async def authored() -> None:
        raise HTTPException(status_code=409, detail="A probe is already running.")

    @app.get("/offline")
    async def offline() -> None:
        raise ConnectionRefusedError(111, "Connection refused")

    return app


async def test_unknown_path_does_not_name_the_framework() -> None:
    transport = ASGITransport(app=_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/__nope")

    assert response.status_code == 404
    assert response.json() == {"error": "not_found"}
    assert "Not Found" not in response.text


async def test_wrong_method_does_not_name_the_framework() -> None:
    transport = ASGITransport(app=_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/")

    assert response.status_code == 405
    assert response.json() == {"error": "method_not_allowed"}


async def test_validation_failure_does_not_echo_input() -> None:
    transport = ASGITransport(app=_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/needs-int?value=abc")

    assert response.status_code == 422
    assert response.json() == {"error": "invalid_request"}
    for leak in ("int_parsing", "loc", "msg", "input", "abc"):
        assert leak not in response.text


async def test_method_not_allowed_keeps_the_allow_header() -> None:
    """RFC 9110 requires Allow on a 405; rewriting the body must not drop it."""
    transport = ASGITransport(app=_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/")

    assert response.status_code == 405
    assert "GET" in (response.headers.get("allow") or "")


async def test_browser_gets_the_real_error_template() -> None:
    """Assert real rendered markup, not just a status code."""
    transport = ASGITransport(app=_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/__nope", headers={"accept": "text/html"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    # Markers only the real template produces; the inline fallback has none.
    assert "bootstrap.min.css" in response.text
    assert "healthmonitor.css" in response.text
    assert "Back to dashboard" in response.text


async def test_unavailable_error_uses_dashboard_wording() -> None:
    """Infrastructure failures name the sole public dashboard consistently."""
    transport = ASGITransport(app=_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/offline", headers={"accept": "text/html"})

    assert response.status_code == 503
    assert "The uptime dashboard is temporarily unavailable" in response.text


async def test_authored_refusal_keeps_its_detail() -> None:
    """Only framework-generated errors are rewritten."""
    transport = ASGITransport(app=_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/authored")

    assert response.status_code == 409
    assert response.json() == {"detail": "A probe is already running."}
