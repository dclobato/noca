#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest presentation launcher route and template contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient
from jinja2 import ChoiceLoader, FileSystemLoader
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import animator.main as animator_main
from tests.animator._feed_seed import make_contest, make_site, make_user
from web.models.users import UberAdmin

pytestmark = pytest.mark.asyncio

_ANIMATOR_DIR = Path(animator_main.__file__).resolve().parent
_SHARED_DIR = _ANIMATOR_DIR.parent / "shared"


def _wire_app(session: AsyncSession) -> None:
    """Attach the test database and production-shaped templates."""
    animator_main.app.state.db_session = async_sessionmaker(
        session.bind,
        expire_on_commit=False,
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
    animator_main.app.state.templates = templates


async def test_launcher_prioritizes_global_links_and_lists_scoped_sites(
    session: AsyncSession,
    uberadmin: UberAdmin,
) -> None:
    """Render global destinations first and a complete link set per site."""
    contest = await make_contest(session, uberadmin, slug="presentation-index")
    north = await make_site(session, contest, sitename="North Campus")
    south = await make_site(session, contest, sitename="South Campus")
    session.add_all(
        [
            make_user(contest, uberadmin, "north-one", site_id=north.id),
            make_user(contest, uberadmin, "north-two", site_id=north.id),
            make_user(contest, uberadmin, "south-one", site_id=south.id),
        ]
    )
    await session.commit()
    _wire_app(session)

    async with AsyncClient(
        transport=ASGITransport(app=animator_main.app),
        base_url="http://test",
    ) as client:
        response = await client.get("/c/presentation-index/")

    assert response.status_code == 200
    html = response.text
    assert html.index("Global presentations") < html.index("North Campus")
    assert "No sites are configured" not in html
    assert "2 teams" in html
    assert "1 team" in html

    base = "http://test/c/presentation-index"
    for scope in ("global", north.id, south.id):
        assert f"{base}/scoreboard?scope={scope}" in html
        assert f"{base}/ceremony?scope={scope}" in html
        assert f"{base}/control?scope={scope}" in html

    assert "animator.js" not in html
    assert "contest-index.css" in html
    assert f'data-start-ms="{int(contest.start_time.timestamp() * 1000)}"' in html
    assert f'data-end-ms="{int(contest.end_time.timestamp() * 1000)}"' in html
    shared_clock = "/static/shared-js/contest-clock-utils.js?v=test"
    launcher_clock = "/static/js/contest-index-clock.js?v=test"
    assert html.index(shared_clock) < html.index(launcher_clock)


async def test_launcher_handles_a_contest_without_sites(
    session: AsyncSession,
    uberadmin: UberAdmin,
) -> None:
    """Keep global presentations available when no site exists."""
    await make_contest(session, uberadmin, slug="presentation-empty")
    await session.commit()
    _wire_app(session)

    async with AsyncClient(
        transport=ASGITransport(app=animator_main.app),
        base_url="http://test",
    ) as client:
        response = await client.get("/c/presentation-empty/")

    assert response.status_code == 200
    assert "Global presentations" in response.text
    assert "No sites are configured for this contest." in response.text


async def test_launcher_keeps_missing_and_disabled_contests_indistinguishable(
    session: AsyncSession,
    uberadmin: UberAdmin,
) -> None:
    """Apply the ordinary enabled-contest gate before rendering."""
    await make_contest(
        session,
        uberadmin,
        slug="presentation-disabled",
        animator_enabled=False,
    )
    await session.commit()
    _wire_app(session)

    async with AsyncClient(
        transport=ASGITransport(app=animator_main.app),
        base_url="http://test",
    ) as client:
        disabled = await client.get("/c/presentation-disabled/")
        missing = await client.get("/c/presentation-missing/")

    assert disabled.status_code == missing.status_code == 404
    assert disabled.json() == missing.json()
