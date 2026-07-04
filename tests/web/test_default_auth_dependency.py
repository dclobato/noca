#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Web default-deny authentication dependency."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

import web.dependencies as web_dependencies
from web.dependencies import enforce_web_default_auth


def _build_app() -> FastAPI:
    """Build a small app with the production Web default auth dependency."""
    app = FastAPI(dependencies=[Depends(enforce_web_default_auth)])

    @app.get("/login")
    async def _login() -> dict[str, str]:
        return {"page": "login"}

    @app.get("/private")
    async def _private() -> dict[str, str]:
        return {"page": "private"}

    @app.get("/c/demo/login")
    async def _contest_login() -> dict[str, str]:
        return {"page": "contest-login"}

    @app.get("/c/demo/private")
    async def _contest_private() -> dict[str, str]:
        return {"page": "contest-private"}

    return app


@pytest.mark.asyncio
async def test_public_web_allowlist_does_not_require_auth() -> None:
    """Public Web auth routes remain reachable without a session."""
    app = _build_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/login")
        contest_response = await client.get("/c/demo/login")

    assert response.status_code == 200
    assert contest_response.status_code == 200


@pytest.mark.asyncio
async def test_default_web_auth_redirects_private_routes() -> None:
    """Unlisted Web routes redirect anonymous users to the right login page."""
    app = _build_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/private", follow_redirects=False)
        contest_response = await client.get("/c/demo/private", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"] == "/login"
    assert contest_response.status_code == 302
    assert contest_response.headers["Location"] == "/c/demo/login"


@pytest.mark.asyncio
async def test_default_web_auth_allows_valid_cached_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A validated token lets the request reach the route handler."""

    def _valid_token(_request: Any) -> SimpleNamespace:
        return SimpleNamespace(valid=True)

    monkeypatch.setattr(web_dependencies, "get_validated_auth_token", _valid_token)
    app = _build_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/private")

    assert response.status_code == 200
    assert response.json() == {"page": "private"}
