#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public Animator contest-index route and template contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient
from jinja2 import ChoiceLoader, FileSystemLoader
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import animator.main as animator_main
from tests.animator._feed_seed import make_contest
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
    templates.env.globals["brand_name"] = "NOCA Animator"
    animator_main.app.state.templates = templates


async def _set_contest_timing(
    session: AsyncSession,
    uberadmin: UberAdmin,
    *,
    slug: str,
    name: str,
    start: datetime,
    duration_minutes: int,
    enabled: bool = True,
    active: bool = True,
) -> None:
    """Seed a contest suitable for route-level lifecycle assertions."""
    contest = await make_contest(
        session,
        uberadmin,
        slug=slug,
        animator_enabled=enabled,
    )
    contest.contest_name = name
    contest.start_time = start
    contest.duration_minutes = duration_minutes
    contest.stop_answers_after = duration_minutes
    contest.active = active


async def test_index_lists_available_contests_and_links_launchers(
    session: AsyncSession,
    uberadmin: UberAdmin,
) -> None:
    """Render lifecycle sections while hiding disabled and archived contests."""
    now = datetime.now(UTC)
    await _set_contest_timing(
        session,
        uberadmin,
        slug="live-show",
        name="Live Show",
        start=now - timedelta(hours=1),
        duration_minutes=180,
    )
    await _set_contest_timing(
        session,
        uberadmin,
        slug="next-show",
        name="Next Show",
        start=now + timedelta(days=2),
        duration_minutes=180,
    )
    await _set_contest_timing(
        session,
        uberadmin,
        slug="past-show",
        name="Past Show",
        start=now - timedelta(days=2),
        duration_minutes=180,
    )
    await _set_contest_timing(
        session,
        uberadmin,
        slug="hidden-disabled",
        name="Hidden Disabled",
        start=now,
        duration_minutes=180,
        enabled=False,
    )
    await _set_contest_timing(
        session,
        uberadmin,
        slug="hidden-archived",
        name="Hidden Archived",
        start=now,
        duration_minutes=180,
        active=False,
    )
    await session.commit()
    _wire_app(session)

    async with AsyncClient(
        transport=ASGITransport(app=animator_main.app),
        base_url="http://test",
    ) as client:
        response = await client.get("/")

    assert response.status_code == 200
    html = response.text
    assert html.index("Live now") < html.index("Upcoming") < html.index("Past contests")
    for slug, name in (
        ("live-show", "Live Show"),
        ("next-show", "Next Show"),
        ("past-show", "Past Show"),
    ):
        assert name in html
        assert f"http://test/c/{slug}/" in html
    assert "Hidden Disabled" not in html
    assert "Hidden Archived" not in html
    assert "<strong>3</strong>" in html
    assert "<span>contests available</span>" in html
    assert "animator-home.css?v=test" in html
    assert "animator.js" not in html


async def test_index_renders_empty_state_without_presentation_scripts(
    session: AsyncSession,
    uberadmin: UberAdmin,
) -> None:
    """Explain how contests appear when none meet both discovery gates."""
    await _set_contest_timing(
        session,
        uberadmin,
        slug="only-disabled",
        name="Only Disabled",
        start=datetime.now(UTC),
        duration_minutes=60,
        enabled=False,
    )
    await session.commit()
    _wire_app(session)

    async with AsyncClient(
        transport=ASGITransport(app=animator_main.app),
        base_url="http://test",
    ) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "No presentations available" in response.text
    assert "Enable Animator for a non-archived contest" in response.text
    assert "<strong>0</strong>" in response.text
    assert "<span>contests available</span>" in response.text
    assert "animator.js" not in response.text
