#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for neutral animator error responses.

The animator registered no exception handlers at all before this, so every 404,
405 and 422 was the raw framework default on public, unauthenticated endpoints.
Its refusals are also load-bearing: an unknown slug, a disabled contest and the
control kill switch must stay indistinguishable, which is only true while every
response is derived from the status code and never from the cause.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient
from jinja2 import ChoiceLoader, FileSystemLoader

from animator.error_handlers import register_error_handlers

pytestmark = pytest.mark.asyncio

_ANIMATOR_DIR = Path(__file__).resolve().parents[2] / "animator"
_SHARED_DIR = Path(__file__).resolve().parents[2] / "shared"


def _app() -> FastAPI:
    """Build an app with the real handlers and the real template environment.

    Real templates matter here: ``render_error_response`` catches any rendering
    failure and falls back to a minimal inline document, so a broken template
    would still produce a 404 and pass a status-only assertion.
    """
    app = FastAPI()
    templates = Jinja2Templates(directory=_ANIMATOR_DIR / "template")
    templates.env.loader = ChoiceLoader(
        [
            FileSystemLoader(str(_ANIMATOR_DIR / "template")),
            FileSystemLoader(str(_SHARED_DIR / "template")),
        ]
    )
    templates.env.globals["app_version"] = "test"
    templates.env.globals["brand_name"] = "NOCA Animator"
    app.state.templates = templates
    register_error_handlers(app)

    # Static mounts the error template resolves through url_for.
    from fastapi.staticfiles import StaticFiles

    app.mount("/static/css", StaticFiles(directory=_ANIMATOR_DIR / "static" / "css"), name="animator_static_css")
    app.mount("/static/js", StaticFiles(directory=_ANIMATOR_DIR / "static" / "js"), name="animator_static_js")
    app.mount("/static/vendor", StaticFiles(directory=_SHARED_DIR / "static" / "vendor"), name="static_vendor")

    @app.get("/only-get")
    async def only_get() -> dict[str, int]:
        return {"ok": 1}

    @app.get("/needs-int")
    async def needs_int(value: int) -> dict[str, int]:
        return {"value": value}

    @app.get("/authored")
    async def authored() -> None:
        raise HTTPException(status_code=409, detail="The ceremony has already started.")

    return app


async def test_unknown_path_is_neutral() -> None:
    transport = ASGITransport(app=_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/__nope", headers={"accept": "application/json"})

    assert response.status_code == 404
    assert response.json() == {"error": "not_found"}


async def test_method_not_allowed_is_neutral_and_keeps_allow() -> None:
    transport = ASGITransport(app=_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/only-get", headers={"accept": "application/json"})

    assert response.status_code == 405
    assert response.json() == {"error": "method_not_allowed"}
    assert "GET" in (response.headers.get("allow") or "")


async def test_validation_failure_does_not_echo_input() -> None:
    transport = ASGITransport(app=_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/needs-int?value=abc", headers={"accept": "application/json"})

    assert response.status_code == 422
    assert response.json() == {"error": "invalid_request"}
    for leak in ("int_parsing", "loc", "msg", "input", "abc"):
        assert leak not in response.text


async def test_browser_gets_the_real_error_template() -> None:
    """Assert real rendered markup, not just a status code.

    ``render_error_response`` swallows template errors into a minimal fallback,
    so only checking the status would hide a broken template entirely.
    """
    transport = ASGITransport(app=_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/__nope", headers={"accept": "text/html"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    # Markers that only the real template produces; the inline fallback has none.
    assert "bootstrap.min.css" in response.text
    assert "animator.css" in response.text
    assert "Page not found" in response.text


async def test_authored_refusal_keeps_its_detail() -> None:
    """The control panel reads refusal messages out of `payload.detail`."""
    transport = ASGITransport(app=_app(), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/authored", headers={"accept": "application/json"})

    assert response.status_code == 409
    assert response.json() == {"detail": "The ceremony has already started."}
