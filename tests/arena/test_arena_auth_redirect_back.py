#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for Arena browser auth redirect-back handling."""

from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient
from starlette.middleware.sessions import SessionMiddleware

from arena.database import get_db
from arena.dependencies.admin import require_arena_admin
from arena.dependencies.auth import get_current_arena_user
from arena.error_handlers import arena_http_exception_handler
from arena.routes.submissions import arena_submission_detail, arena_submission_request_ai_review
from arena.routes.user_security import arena_2fa_confirm, arena_2fa_setup
from arena.routes.users import arena_user_profile, arena_user_profile_notification_delete
from shared.enumerations import ArenaRole


async def _empty_db() -> Any:
    """Yield a dummy dependency value for tests that redirect before DB use."""
    yield None


def _build_exception_app(*, current_user: Any = None) -> FastAPI:
    """Build a small app that uses the production HTTPException handler."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")
    app.add_exception_handler(HTTPException, arena_http_exception_handler)

    async def _current_user_override() -> Any:
        return current_user

    app.dependency_overrides[get_current_arena_user] = _current_user_override
    app.dependency_overrides[get_db] = _empty_db

    @app.get("/dashboard", name="arena_dashboard")
    async def _dashboard(request: Request) -> Response:
        messages = request.session.get("_flash_messages", [])
        return Response(f"dashboard {messages}")

    @app.get("/auth/login", name="arena_login")
    async def _login(request: Request) -> Response:
        messages = request.session.get("_flash_messages", [])
        return Response(f"login next={request.query_params.get('next')} {messages}")

    @app.get("/admin/page")
    async def _admin_page(current_user: Any = Depends(require_arena_admin)) -> Response:
        return Response(f"admin {current_user.id}")

    @app.get("/missing")
    async def _missing() -> Response:
        raise HTTPException(status_code=400, detail="Missing page")

    return app


def _build_url_app() -> FastAPI:
    """Build a tiny app with the named routes needed for redirect URL building."""
    app = FastAPI()

    @app.get("/dashboard", name="arena_dashboard")
    async def _dashboard() -> Response:
        return Response("dashboard")

    @app.get("/auth/login", name="arena_login")
    async def _login() -> Response:
        return Response("login")

    @app.get("/problems/{arena_number}", name="arena_problem_detail")
    async def _problem_detail(arena_number: int) -> Response:
        return Response(f"problem {arena_number}")

    @app.get("/submissions/{submission_id}", name="arena_submission_detail")
    async def _submission_detail(submission_id: str) -> Response:
        return Response(f"submission {submission_id}")

    @app.get("/user/profile", name="arena_user_profile")
    async def _profile() -> Response:
        return Response("profile")

    @app.get("/user/profile/2fa/setup", name="arena_2fa_setup")
    async def _2fa_setup() -> Response:
        return Response("2fa setup")

    return app


def _make_request(app: FastAPI, method: str, path: str) -> Request:
    """Create a minimal Starlette request for direct route-handler tests."""
    parsed = urlparse(path)
    return Request(
        {
            "type": "http",
            "method": method,
            "path": parsed.path,
            "query_string": parsed.query.encode(),
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 123),
            "app": app,
            "router": app.router,
        }
    )


@pytest.mark.asyncio
async def test_browser_401_redirects_to_login_with_next_and_clears_cookie() -> None:
    """Browser 401 responses redirect to login with the current path and query."""
    app = _build_exception_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": "stale"},
    ) as client:
        response = await client.get(
            "/admin/page?tab=users",
            headers={"Accept": "text/html"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "http://testserver/auth/login?next=%2Fadmin%2Fpage%3Ftab%3Dusers"
    assert "arena_access_token" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_browser_401_flash_is_available_after_redirect() -> None:
    """The 401 handler stores a flash message for the login page."""
    app = _build_exception_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/admin/page",
            headers={"Accept": "text/html"},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert "Please log in to continue." in response.text


@pytest.mark.asyncio
async def test_htmx_401_uses_full_page_redirect_header() -> None:
    """HTMX auth failures navigate to login instead of swapping a 401 payload."""
    app = _build_exception_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": "stale"},
    ) as client:
        response = await client.get(
            "/admin/page?tab=workers",
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )

    assert response.status_code == 401
    assert response.headers["hx-redirect"] == ("http://testserver/auth/login?next=%2Fadmin%2Fpage%3Ftab%3Dworkers")
    assert "location" not in response.headers
    assert "arena_access_token" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_browser_403_redirects_to_dashboard_with_flash() -> None:
    """Browser 403 responses redirect to dashboard and flash a permission message."""
    current_user = SimpleNamespace(id="user-1", role=ArenaRole.ARENA_USER)
    app = _build_exception_app(current_user=current_user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/admin/page",
            headers={"Accept": "text/html"},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert response.url.path == "/dashboard"
    assert "You do not have permission to access that page." in response.text


@pytest.mark.asyncio
async def test_htmx_403_uses_full_page_redirect_header() -> None:
    """HTMX permission failures navigate to the Arena dashboard."""
    current_user = SimpleNamespace(id="user-1", role=ArenaRole.ARENA_USER)
    app = _build_exception_app(current_user=current_user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/admin/page",
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )

    assert response.status_code == 403
    assert response.headers["hx-redirect"] == "http://testserver/dashboard"
    assert "location" not in response.headers


@pytest.mark.asyncio
async def test_non_auth_http_exception_keeps_default_response() -> None:
    """Non-special-cased HTTP exceptions (not 401/403/404) delegate to FastAPI's default handler."""
    app = _build_exception_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/missing", headers={"Accept": "text/html"})

    assert response.status_code == 400
    assert response.json() == {"detail": "Missing page"}


@pytest.mark.asyncio
async def test_json_admin_dependency_401_stays_json() -> None:
    """API callers that do not request HTML keep JSON 401 responses."""
    app = _build_exception_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/admin/page", headers={"Accept": "application/json"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}


@pytest.mark.asyncio
async def test_json_admin_dependency_403_stays_json() -> None:
    """API callers that do not request HTML keep JSON 403 responses."""
    current_user = SimpleNamespace(id="user-1", role=ArenaRole.ARENA_USER)
    app = _build_exception_app(current_user=current_user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/admin/page", headers={"Accept": "application/json"})

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "method", "path", "expected_next"),
    [
        ("profile", "GET", "/user/profile?tab=submissions", "/user/profile?tab=submissions"),
        (
            "profile_notification_delete",
            "POST",
            "/user/profile/notifications/notif-1/delete",
            "/user/profile?tab=notifications",
        ),
        ("security_setup", "GET", "/user/profile/2fa/setup", "/user/profile/2fa/setup"),
        ("security_confirm", "POST", "/user/profile/2fa/confirm", "/user/profile/2fa/setup"),
        ("submission_detail", "GET", "/submissions/sub-1?source=queue", "/submissions/sub-1?source=queue"),
        (
            "submission_ai_review",
            "POST",
            "/submissions/sub-1/request-ai-review",
            "/submissions/sub-1",
        ),
    ],
)
async def test_manual_protected_page_redirects_include_next(
    handler_name: str,
    method: str,
    path: str,
    expected_next: str,
) -> None:
    """Protected page routes use the shared login redirect helper for guests."""
    app = _build_url_app()
    request = _make_request(app, method, path)

    def flash(message: str, category: object) -> None:
        """Ignore flash calls after auth passes; guest paths return before use."""
        return None

    if handler_name == "profile":
        response = await arena_user_profile(request, current_user=None, session=None)
    elif handler_name == "profile_notification_delete":
        response = await arena_user_profile_notification_delete("notif-1", request, flash, None, None)
    elif handler_name == "security_setup":
        response = await arena_2fa_setup(request, flash, current_user=None, session=None)
    elif handler_name == "security_confirm":
        response = await arena_2fa_confirm(request, flash, current_user=None, session=None)
    elif handler_name == "submission_detail":
        response = await arena_submission_detail("sub-1", request, current_user=None, session=None)
    else:
        response = await arena_submission_request_ai_review("sub-1", request, flash, None, None)

    assert response.status_code == 303
    location = urlparse(response.headers["location"])
    assert location.path == "/auth/login"
    assert parse_qs(location.query) == {"next": [expected_next]}
