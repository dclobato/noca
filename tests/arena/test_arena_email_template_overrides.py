#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests that Arena's catalogue honours the configured override directory."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from starlette.middleware.sessions import SessionMiddleware

from arena.config import settings
from arena.dependencies.admin import require_arena_admin
from arena.email_templates import (
    arena_email_templates,
    render_email,
    validate_email_template_overrides,
)
from arena.routes.admin_dashboard_email_templates import router as arena_email_templates_router
from shared.enumerations import ArenaRole
from shared.services.email_templates import EmailTemplateError


class _RecordingTemplates:
    """Capture the template call instead of rendering `_base.html`.

    Arena's base template resolves around twenty route names, so rendering a
    full page under test would mean mounting a dozen unrelated routers. The
    other Arena admin page tests stub the environment for the same reason; what
    matters here is that the request reaches the handler with the guard applied
    and the right context.
    """

    def __init__(self) -> None:
        self.captured: dict[str, object] = {}

    def TemplateResponse(self, request: object, name: str, context: dict[str, object]) -> HTMLResponse:  # noqa: N802
        """Record the render request and answer with an empty page."""
        self.captured.update(request=request, name=name, context=context)
        return HTMLResponse("")


def _build_app(*, authorized: bool = True) -> tuple[FastAPI, _RecordingTemplates]:
    """Build a minimal Arena app serving the visibility route."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-session-secret")
    templates = _RecordingTemplates()
    app.state.arena_templates = templates
    app.include_router(arena_email_templates_router)

    if authorized:
        app.dependency_overrides[require_arena_admin] = _fake_admin
    else:

        async def _reject() -> None:
            raise HTTPException(status_code=403)

        app.dependency_overrides[require_arena_admin] = _reject
    return app, templates


async def _fake_admin() -> SimpleNamespace:
    """Return the minimum admin object the Arena templates read."""
    return SimpleNamespace(
        id="admin-1",
        email="admin@example.com",
        role=ArenaRole.ARENA_ADMIN,
        can_edit=True,
        nome="Admin",
        dta_foto=None,
    )


@pytest.mark.asyncio
async def test_arena_page_serves_the_arena_registry_to_an_admin(override_root: Path) -> None:
    """An authorized request reaches the handler with the module's own templates."""
    app, templates = _build_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/email-templates")

    assert response.status_code == 200
    assert templates.captured["name"] == "admin/dashboard_email_templates.html"
    context = templates.captured["context"]
    assert isinstance(context, dict)
    views = context["email_templates"]
    assert {view.key for view in views} >= {"reset_password"}
    assert all(view.source == "default" for view in views)


@pytest.mark.asyncio
async def test_arena_page_is_refused_without_the_admin_guard(override_root: Path) -> None:
    """The guard runs in the request path, rather than merely being declared."""
    app, _templates = _build_app(authorized=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/email-templates")

    assert response.status_code == 403


def test_the_arena_page_is_read_only() -> None:
    """No write path may reach the template tree from the admin surface."""
    routes = [route for route in arena_email_templates_router.routes if isinstance(route, APIRoute)]

    assert [route.methods for route in routes] == [{"GET"}]
    assert all(any(dep.call is require_arena_admin for dep in route.dependant.dependencies) for route in routes)


_OVERRIDE = 'subject = "Nova senha"\nbody = "Ola {nome}, use {url} em uma hora."\n'


@pytest.fixture
def override_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point Arena at an empty override root for one test."""
    root = tmp_path / "email_overrides"
    (root / "arena").mkdir(parents=True)
    (root / "web").mkdir(parents=True)
    monkeypatch.setattr(settings, "EMAIL_TEMPLATE_OVERRIDE_DIR", root)
    arena_email_templates.cache_clear()
    yield root
    arena_email_templates.cache_clear()


def test_published_override_changes_what_arena_sends(override_root: Path) -> None:
    """A valid file in the arena namespace replaces subject and body together."""
    (override_root / "arena" / "reset_password.toml").write_text(_OVERRIDE, encoding="utf-8")

    rendered = render_email("reset_password", nome="Ada", url="https://arena.example/reset")

    assert rendered.subject == "Nova senha"
    assert rendered.body.startswith("Ola Ada")


def test_web_namespace_does_not_reach_arena(override_root: Path) -> None:
    """The two modules share a mount and must not share wording."""
    (override_root / "web" / "reset_password.toml").write_text(_OVERRIDE, encoding="utf-8")

    rendered = render_email("reset_password", nome="Ada", url="https://arena.example/reset")

    assert rendered.subject != "Nova senha"


def test_startup_refuses_an_invalid_override(override_root: Path) -> None:
    """An undeclared placeholder must stop the start, naming file and reason."""
    (override_root / "arena" / "reset_password.toml").write_text(
        'subject = "Nova senha"\nbody = "Ola {nome}, use {url}. {assinatura}"\n', encoding="utf-8"
    )

    with pytest.raises(EmailTemplateError, match="unknown placeholders: assinatura"):
        validate_email_template_overrides()
