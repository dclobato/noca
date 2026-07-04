#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Arena default-deny authentication gate.

Covers both the pure path-allowlist predicate and the end-to-end behaviour of
the global ``enforce_arena_authentication`` dependency wired into a small app
that reuses the production HTTPException handler.
"""

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from arena.dependencies.access_control import (
    _is_public_arena_path,
    enforce_arena_authentication,
)
from arena.error_handlers import arena_http_exception_handler


class _FakeAuthMiddleware:
    """Populate ``request.state`` like ``ArenaAuthMiddleware`` would, from headers.

    ``X-Test-Auth: yes`` marks the request as carrying a valid session token;
    ``X-Test-Cap: yes`` marks the remember-me absolute cap as exceeded.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            scope.setdefault("state", {})
            headers = dict(scope["headers"])
            authed = headers.get(b"x-test-auth") == b"yes"
            scope["state"]["validated_token"] = object() if authed else None
            scope["state"]["token_cap_exceeded"] = headers.get(b"x-test-cap") == b"yes"
        await self.app(scope, receive, send)


def _build_app() -> FastAPI:
    """Build an app with the global gate and the production 401 handler."""
    app = FastAPI(dependencies=[Depends(enforce_arena_authentication)])
    app.add_exception_handler(HTTPException, arena_http_exception_handler)
    app.add_middleware(_FakeAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    @app.get("/auth/login", name="arena_login")
    async def _login(request: Request) -> Response:
        return Response(f"login next={request.query_params.get('next')}")

    @app.get("/dashboard", name="arena_dashboard")
    async def _dashboard() -> Response:
        return Response("dashboard")

    @app.get("/health", name="arena_health")
    async def _health() -> Response:
        return Response("ok")

    @app.get("/favicon.ico", name="arena_favicon")
    async def _favicon() -> Response:
        return Response("icon")

    @app.get("/problems", name="arena_problems")
    async def _problems() -> Response:
        return Response("problems")

    @app.get("/problems/{number}", name="arena_problem_detail")
    async def _problem_detail(number: int) -> Response:
        return Response("problem-detail")

    return app


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/dashboard",
        "/health",
        "/problems",
        "/favicon.ico",
        "/site.webmanifest",
        "/legal",
        "/legal/terms",
        "/help",
        "/help/rating",
        "/auth/login",
        "/auth/signup",
        "/auth/activate",
        "/auth/2fa",
    ],
)
def test_public_paths_are_public(path: str) -> None:
    """Allowlisted paths and namespaces resolve as public."""
    assert _is_public_arena_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "/legalish",
        "/helpdesk",
        "/authorize",
        "/problems/42",
        "/problems/42/statistics",
        "/ranking",
        "/live",
        "/profile/1",
        "/submissions/abc",
        "/status",
        "/admin/users",
    ],
)
def test_non_public_paths_are_protected(path: str) -> None:
    """Everything outside the allowlist (incl. near-prefix misses) is protected."""
    assert _is_public_arena_path(path) is False


@pytest.mark.asyncio
async def test_logged_out_protected_path_redirects_to_login() -> None:
    """A guest hitting a protected page is bounced to login with a next param."""
    app = _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/problems/42?tab=all",
            headers={"Accept": "text/html"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "http://testserver/auth/login?next=%2Fproblems%2F42%3Ftab%3Dall"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/dashboard", "/problems", "/health", "/favicon.ico", "/auth/login"])
async def test_logged_out_public_paths_are_reachable(path: str) -> None:
    """Public paths return their own 200 response without redirecting to login."""
    app = _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(path, headers={"Accept": "text/html"}, follow_redirects=False)

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_logged_out_htmx_protected_path_uses_hx_redirect() -> None:
    """HTMX guests get a 401 with HX-Redirect instead of an HTML swap."""
    app = _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/problems/42",
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )

    assert response.status_code == 401
    assert response.headers["hx-redirect"] == "http://testserver/auth/login?next=%2Fproblems%2F42"
    assert "location" not in response.headers


@pytest.mark.asyncio
async def test_logged_in_user_reaches_protected_path() -> None:
    """A valid session token admits the request to a protected page."""
    app = _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/problems/42",
            headers={"Accept": "text/html", "X-Test-Auth": "yes"},
            follow_redirects=False,
        )

    assert response.status_code == 200
    assert response.text == "problem-detail"


@pytest.mark.asyncio
async def test_session_cap_exceeded_is_treated_as_logged_out() -> None:
    """A token past its remember-me absolute cap is denied like a guest."""
    app = _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/problems/42",
            headers={"Accept": "text/html", "X-Test-Auth": "yes", "X-Test-Cap": "yes"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("http://testserver/auth/login")


@pytest.mark.asyncio
async def test_logged_in_user_also_passes_public_path() -> None:
    """The gate never blocks public paths regardless of auth state."""
    app = _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/dashboard", headers={"X-Test-Auth": "yes"})

    assert response.status_code == 200
    assert response.text == "dashboard"


def test_enforce_skips_db_dependency() -> None:
    """The gate must not declare a get_db/get_current_arena_user dependency.

    Public paths and /health must open no database session, so the gate reads
    only request.state and takes no DB-bound sub-dependencies.
    """
    import inspect

    params = inspect.signature(enforce_arena_authentication).parameters
    assert list(params) == ["request"]
