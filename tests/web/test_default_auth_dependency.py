#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
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

    @app.post("/c/demo/private")
    async def _contest_private_save() -> dict[str, str]:
        return {"page": "contest-private-save"}

    @app.get("/problem-set/demo.zip")
    async def _problem_set() -> dict[str, str]:
        return {"page": "problem-set"}

    @app.get("/announcements")
    async def _announcements() -> dict[str, str]:
        return {"page": "announcements"}

    @app.get("/announcements/some-id")
    async def _announcement_detail() -> dict[str, str]:
        return {"page": "announcement-detail"}

    return app


@pytest.mark.asyncio
async def test_public_web_allowlist_does_not_require_auth() -> None:
    """Public Web auth routes remain reachable without a session."""
    app = _build_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/login")
        contest_response = await client.get("/c/demo/login")
        problem_set_response = await client.get("/problem-set/demo.zip")
        announcements_response = await client.get("/announcements")
        announcement_detail_response = await client.get("/announcements/some-id")

    assert response.status_code == 200
    assert contest_response.status_code == 200
    assert problem_set_response.status_code == 200
    assert announcements_response.status_code == 200
    assert announcement_detail_response.status_code == 200


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
    assert contest_response.headers["Location"] == "/c/demo/login?next=/c/demo/private"


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


@pytest.mark.asyncio
async def test_contest_auth_redirect_carries_the_page_to_return_to() -> None:
    """A bounced GET names itself; a bounced POST names the page it came from."""
    app = _build_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        get_response = await client.get("/c/demo/private?tab=statement")
        post_response = await client.post(
            "/c/demo/private",
            headers={"Referer": "http://test/c/demo/admin/problems/p1/edit?tab=statement"},
        )
        foreign_referer = await client.post("/c/demo/private", headers={"Referer": "http://evil.example/c/demo/admin"})
        other_contest_referer = await client.post("/c/demo/private", headers={"Referer": "http://test/c/other/x"})

    assert get_response.status_code == 302
    assert get_response.headers["location"] == "/c/demo/login?next=/c/demo/private?tab=statement"
    assert post_response.headers["location"] == "/c/demo/login?next=/c/demo/admin/problems/p1/edit?tab=statement"
    assert foreign_referer.headers["location"] == "/c/demo/login"
    assert other_contest_referer.headers["location"] == "/c/demo/login"
