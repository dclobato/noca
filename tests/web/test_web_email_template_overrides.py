#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests that Web's catalogue honours the configured override directory."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.email_templates import EmailTemplateError
from tests.web.test_inactive_contest_routes import _build_app
from web.config import Settings, settings
from web.dependencies import get_uberadmin
from web.email_templates import (
    render_email,
    validate_email_template_overrides,
    web_email_templates,
)
from web.routes.uberadmin_email_templates import router as uberadmin_email_templates_router


async def _fake_uberadmin() -> SimpleNamespace:
    """Return the minimum UberAdmin object the dashboard templates read."""
    return SimpleNamespace(id=1, username="uber", fullname="Uber Admin", is_enabled=True)


@pytest.mark.asyncio
async def test_uberadmin_page_renders_the_web_registry_for_an_uberadmin(
    session: AsyncSession,
    override_root: Path,
) -> None:
    """The page renders the real template from the process's own registry."""
    app, _auth = _build_app(session)
    app.dependency_overrides[get_uberadmin] = _fake_uberadmin

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/uberadmin/email-templates")

    assert response.status_code == 200
    assert "send_credentials" in response.text
    assert "Shipped default" in response.text


@pytest.mark.asyncio
async def test_uberadmin_page_is_refused_without_the_uberadmin_guard(
    session: AsyncSession,
    override_root: Path,
) -> None:
    """The guard runs in the request path, rather than merely being declared.

    Asserting the dependency by position proves nothing about enforcement: this
    sends a real request and lets the overridden guard refuse it.
    """
    app, _auth = _build_app(session)

    async def _reject() -> None:
        raise HTTPException(status_code=403)

    app.dependency_overrides[get_uberadmin] = _reject

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/uberadmin/email-templates")

    assert response.status_code == 403


def test_the_page_is_read_only() -> None:
    """No write path may reach the template tree from the admin surface."""
    routes = [route for route in uberadmin_email_templates_router.routes if isinstance(route, APIRoute)]

    assert [route.methods for route in routes] == [{"GET"}]
    assert all(any(dep.call is get_uberadmin for dep in route.dependant.dependencies) for route in routes)


@pytest.fixture
def override_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point Web at an empty override root for one test.

    The registry is process-wide and cached, so the cache is cleared on both
    sides of the test: the configured directory is read when it is built.
    """
    root = tmp_path / "email_overrides"
    (root / "web").mkdir(parents=True)
    monkeypatch.setattr(settings, "EMAIL_TEMPLATE_OVERRIDE_DIR", root)
    web_email_templates.cache_clear()
    yield root
    web_email_templates.cache_clear()


def test_packaged_default_is_used_when_no_override_exists(override_root: Path) -> None:
    """An empty namespace changes nothing about what Web sends."""
    rendered = render_email(
        "send_credentials",
        fullname="Ada Lovelace",
        contest_name="NOCA Invitational",
        contest_login_url="https://contest.example/login",
        username="ada",
        password="example-password",
        sender_name="NOCA Operations",
    )

    assert "ada" in rendered.body
    assert "Custom credentials" not in rendered.subject


def test_published_override_changes_what_web_sends(override_root: Path) -> None:
    """A valid file in the web namespace replaces subject and body together."""
    (override_root / "web" / "send_credentials.toml").write_text(
        'subject = "Custom credentials"\n'
        'body = "Hi {fullname}, sign in at {contest_login_url} as {username}/{password}."\n',
        encoding="utf-8",
    )

    rendered = render_email(
        "send_credentials",
        fullname="Ada Lovelace",
        contest_name="NOCA Invitational",
        contest_login_url="https://contest.example/login",
        username="ada",
        password="example-password",
        sender_name="NOCA Operations",
    )

    assert rendered.subject == "Custom credentials"
    assert rendered.body.startswith("Hi Ada Lovelace")


def test_startup_refuses_an_unknown_key_in_the_namespace(override_root: Path) -> None:
    """A filename that is not a catalogue key is an operator mistake, not a no-op."""
    (override_root / "web" / "welcome_aboard.toml").write_text(
        'subject = "Welcome"\nbody = "Hello."\n', encoding="utf-8"
    )

    with pytest.raises(EmailTemplateError, match="welcome_aboard.toml"):
        validate_email_template_overrides()


def test_startup_accepts_a_valid_namespace(override_root: Path) -> None:
    """The published override above must not be what stops a deploy."""
    (override_root / "web" / "send_credentials.toml").write_text(
        'subject = "Custom credentials"\n'
        'body = "Hi {fullname}, sign in at {contest_login_url} as {username}/{password}."\n',
        encoding="utf-8",
    )

    validate_email_template_overrides()


def test_startup_is_a_no_op_without_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default install performs no filesystem work at all."""
    monkeypatch.setattr(settings, "EMAIL_TEMPLATE_OVERRIDE_DIR", None)

    validate_email_template_overrides()


def test_blank_configuration_disables_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """The layered templates ship the variable empty; that must mean "off".

    Without the before-validator an empty string validates as ``Path(".")`` -- a
    directory that exists -- and the working directory silently becomes the
    override root.
    """
    monkeypatch.setenv("NOCA_EMAIL_TEMPLATE_OVERRIDE_DIR", "   ")

    assert Settings().EMAIL_TEMPLATE_OVERRIDE_DIR is None


def test_configured_directory_must_exist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A typo in the mount path fails configuration rather than sending defaults."""
    monkeypatch.setenv("NOCA_EMAIL_TEMPLATE_OVERRIDE_DIR", str(tmp_path / "absent"))

    with pytest.raises(ValidationError):
        Settings()


def test_configured_directory_is_accepted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An existing directory reaches the setting as a path, not a string."""
    monkeypatch.setenv("NOCA_EMAIL_TEMPLATE_OVERRIDE_DIR", str(tmp_path))

    configured = Settings().EMAIL_TEMPLATE_OVERRIDE_DIR

    assert configured == tmp_path
