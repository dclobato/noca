#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route behavior of the operator's HTML shell.

Kept apart from ``test_control_routes.py`` (already past a thousand lines) and
from ``test_reveal_templates.py``, which covers the rendered markup. What is
pinned here is the *route*: it takes no credential, it inherits the command
API's gates in the same order, and — because fetching a page is not a command
attempt — it emits no audit record.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient
from jinja2 import ChoiceLoader, FileSystemLoader
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import animator.main as animator_main
from animator.config import settings
from animator.routes.control import router as control_router
from animator.routes.control_page import router as control_page_router
from animator.routes.public import router as public_router
from tests.animator._feed_seed import make_contest
from tests.animator._reveal_seed import seed_ceremony
from web.models.users import UberAdmin

pytestmark = pytest.mark.asyncio

_ANIMATOR_DIR = Path(animator_main.__file__).resolve().parent
_SHARED_DIR = _ANIMATOR_DIR.parent / "shared"


@pytest.fixture(autouse=True)
def _control_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the deployment kill switch on for every test but the one that pins it off.

    The switch defaults to off, so without this the shell would answer ``404``
    everywhere and the tests below would pass or fail on whether the developer's
    ``.env`` happens to enable control.
    """
    monkeypatch.setattr(settings, "ENABLE_CONTROL", True)


def _build_app(engine: AsyncEngine) -> FastAPI:
    """Wire an app carrying the shell and every router whose URLs it builds.

    The page renders the command endpoints and the public ``/meta`` feed as data
    attributes, so those routers must be mounted for ``url_for`` to resolve —
    exactly as ``animator.main`` mounts them. Mounting the command router here
    also makes the "not audited" assertion meaningful: its audit boundary is
    present and still produces no record for a page load, because the shell
    belongs to a different router.
    """
    app = FastAPI()
    app.state.db_session = async_sessionmaker(engine, expire_on_commit=False)
    app.include_router(public_router)
    app.include_router(control_page_router)
    app.include_router(control_router)
    app.mount(
        "/static/css",
        StaticFiles(directory=_ANIMATOR_DIR / "static" / "css"),
        name="animator_static_css",
    )
    app.mount(
        "/static/js",
        StaticFiles(directory=_ANIMATOR_DIR / "static" / "js"),
        name="animator_static_js",
    )
    app.mount(
        "/static/img",
        StaticFiles(directory=_ANIMATOR_DIR / "static" / "img"),
        name="animator_static_img",
    )
    app.mount(
        "/static/shared-js",
        StaticFiles(directory=_SHARED_DIR / "static" / "js"),
        name="static_shared_js",
    )
    app.mount(
        "/static/vendor",
        StaticFiles(directory=_SHARED_DIR / "static" / "vendor"),
        name="static_vendor",
    )

    templates = Jinja2Templates(directory=str(_ANIMATOR_DIR / "template"))
    templates.env.loader = ChoiceLoader(
        [
            FileSystemLoader(str(_ANIMATOR_DIR / "template")),
            FileSystemLoader(str(_SHARED_DIR / "template")),
        ]
    )
    templates.env.globals["app_version"] = "test"
    templates.env.globals["brand_name"] = "NOCA"
    app.state.templates = templates
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_the_shell_takes_no_credential_parameter(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        plain = await client.get(f"/c/{ceremony.slug}/control")
        # A secret in the query string is simply ignored — there is no such
        # parameter — so it can never reach a log, a Referer, or history via a
        # server-side round trip.
        with_secret = await client.get(f"/c/{ceremony.slug}/control?secret=hunter2")

    assert plain.status_code == with_secret.status_code == 200
    assert "hunter2" not in with_secret.text
    assert plain.text == with_secret.text


async def test_unknown_and_disabled_contests_are_the_same_bare_404(session: AsyncSession, uberadmin: UberAdmin) -> None:
    await seed_ceremony(session, uberadmin)
    disabled = await make_contest(session, uberadmin, slug="page-ctl-off", animator_enabled=False)
    await session.commit()
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        unknown = await client.get("/c/page-ctl-missing/control")
        off = await client.get(f"/c/{disabled.login_slug}/control")

    assert unknown.status_code == off.status_code == 404
    assert unknown.json() == off.json()


async def test_the_kill_switch_hides_the_shell(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    monkeypatch.setattr(settings, "ENABLE_CONTROL", False)
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(f"/c/{ceremony.slug}/control")
        unknown = await client.get("/c/page-ctl-missing/control")

    # Same gate, same order, same bare 404 as the command API: deployment
    # configuration is never disclosed.
    assert response.status_code == 404
    assert response.json() == unknown.json()


async def test_fetching_the_shell_is_not_audited_as_a_command_attempt(
    session: AsyncSession, uberadmin: UberAdmin, caplog: pytest.LogCaptureFixture
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    app = _build_app(session.bind)  # type: ignore[arg-type]

    with caplog.at_level(logging.INFO):
        async with _client(app) as client:
            response = await client.get(f"/c/{ceremony.slug}/control")

    assert response.status_code == 200
    # The audit boundary belongs to the command router. Recording page loads as
    # attempts would bury the real ones.
    assert "animator_control_attempt" not in caplog.text


async def test_the_shell_is_reachable_through_the_real_app(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The router is actually registered in ``animator.main``, not just importable."""
    ceremony = await seed_ceremony(session, uberadmin)
    animator_main.app.state.db_session = async_sessionmaker(session.bind, expire_on_commit=False)  # type: ignore[arg-type]
    animator_main.app.state.templates = _build_app(session.bind).state.templates  # type: ignore[arg-type]

    async with _client(animator_main.app) as client:
        response = await client.get(f"/c/{ceremony.slug}/control")

    assert response.status_code == 200
    assert 'id="control-app"' in response.text
