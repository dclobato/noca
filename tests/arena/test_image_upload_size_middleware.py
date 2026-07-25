#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for Arena fail-fast image upload size enforcement."""

from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from starlette.middleware.sessions import SessionMiddleware

from arena.error_handlers import arena_http_exception_handler
from arena.image_upload_limits import arena_image_upload_rules
from shared.services.multipart_file_size import (
    MultipartFileSizeLimitMiddleware,
    MultipartFileSizeRule,
)

_TEST_FILE_LIMIT = 16


def _small_arena_rules() -> tuple[MultipartFileSizeRule, ...]:
    """Return production route rules with small byte ceilings for tests."""
    return tuple(
        MultipartFileSizeRule(
            path_pattern=rule.path_pattern,
            max_file_size=_TEST_FILE_LIMIT,
            label=rule.label,
            field_names=rule.field_names,
        )
        for rule in arena_image_upload_rules(_TEST_FILE_LIMIT)
    )


def _build_app() -> tuple[FastAPI, dict[str, bool]]:
    """Build a minimal Arena app protected by the production route patterns."""
    app = FastAPI()
    app.add_exception_handler(HTTPException, arena_http_exception_handler)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")
    app.add_middleware(MultipartFileSizeLimitMiddleware, rules=_small_arena_rules())
    route_called = {"signup": False, "profile": False, "logo": False, "problem": False}

    @app.get("/", name="arena_dashboard")
    async def dashboard(request: Request) -> dict[str, object]:
        """Expose Arena warning flashes for redirect assertions."""
        return {"messages": request.session.pop("_flash_messages", [])}

    @app.get("/user/profile")
    async def profile_page(request: Request) -> dict[str, object]:
        """Expose warning flashes on the profile return path."""
        return {"messages": request.session.pop("_flash_messages", [])}

    @app.post("/auth/signup")
    async def signup(request: Request) -> dict[str, bool]:
        """Parse a protected signup form."""
        await request.form()
        route_called["signup"] = True
        return {"ok": True}

    @app.post("/user/profile/photo")
    async def profile_photo(request: Request) -> dict[str, bool]:
        """Parse a protected profile-photo form."""
        await request.form()
        route_called["profile"] = True
        return {"ok": True}

    @app.post("/admin/affiliations/{affiliation_id}/logo")
    async def affiliation_logo(
        affiliation_id: str,
        request: Request,
    ) -> dict[str, bool | str]:
        """Parse a protected affiliation-logo form."""
        await request.form()
        route_called["logo"] = True
        return {"ok": True, "affiliation_id": affiliation_id}

    @app.post("/admin/problems/new")
    async def problem_new(request: Request) -> dict[str, bool]:
        """Parse a mixed problem-create multipart form."""
        await request.form()
        route_called["problem"] = True
        return {"ok": True}

    @app.post("/admin/problems/{problem_id}/edit")
    async def problem_edit(problem_id: str, request: Request) -> dict[str, bool | str]:
        """Parse a mixed problem-edit multipart form."""
        await request.form()
        route_called["problem"] = True
        return {"ok": True, "problem_id": problem_id}

    return app, route_called


async def test_signup_photo_returns_413_for_api_client() -> None:
    """Arena signup stops an oversized original photo before the route runs."""
    app, route_called = _build_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/auth/signup",
            files={"profile_photo": ("photo.png", b"x" * 17, "image/png")},
        )

    assert response.status_code == 413
    assert response.json()["detail"].startswith("Profile photo file too large.")
    assert route_called["signup"] is False


async def test_profile_photo_redirects_browser_with_warning() -> None:
    """Arena profile uploads return HTML users to the same page with a warning."""
    app, route_called = _build_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/user/profile/photo",
            files={"foto_cropada": ("photo.png", b"x" * 17, "image/png")},
            headers={
                "accept": "text/html",
                "referer": "http://testserver/user/profile",
            },
        )
        profile_response = await client.get(response.headers["location"])

    assert response.status_code == 303
    assert response.headers["location"] == "/user/profile"
    assert profile_response.json()["messages"] == [
        {
            "category": "warning",
            "message": "Profile photo file too large. Maximum allowed: 0.0 MB.",
        }
    ]
    assert route_called["profile"] is False


async def test_affiliation_logo_returns_ajax_error_shape() -> None:
    """Oversized logos retain the JSON contract expected by the Arena modal."""
    app, route_called = _build_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/admin/affiliations/aff-1/logo",
            files={"foto_cropada": ("logo.png", b"x" * 17, "image/png")},
        )

    assert response.status_code == 413
    assert response.json() == {
        "ok": False,
        "error": "Affiliation logo file too large. Maximum allowed: 0.0 MB.",
    }
    assert route_called["logo"] is False


async def test_problem_limit_ignores_validator_source_file() -> None:
    """A large validator upload does not count toward the problem-image ceiling."""
    app, route_called = _build_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/admin/problems/new",
            files={
                "image": ("image.png", b"x" * 16, "image/png"),
                "validator_source_file": ("validator.py", b"x" * 17, "text/plain"),
            },
        )

    assert response.status_code == 200
    assert route_called["problem"] is True


async def test_problem_create_and_edit_reject_oversized_image() -> None:
    """Both Arena problem-save paths apply the image-only streaming rule."""
    app, route_called = _build_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/admin/problems/new",
            files={"image": ("image.png", b"x" * 17, "image/png")},
        )
        edit_response = await client.post(
            "/admin/problems/problem-1/edit",
            files={"image": ("image.png", b"x" * 17, "image/png")},
        )

    assert create_response.status_code == 413
    assert edit_response.status_code == 413
    assert route_called["problem"] is False
