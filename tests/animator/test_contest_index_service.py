#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Animator contest-index query and lifecycle contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from animator.services.contest_index_service import list_animator_contests
from tests.animator._feed_seed import make_contest
from web.models.users import UberAdmin

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 7, 31, 15, 0, tzinfo=UTC)


async def _contest_at(
    session: AsyncSession,
    uberadmin: UberAdmin,
    *,
    slug: str,
    start: datetime,
    duration_minutes: int,
    enabled: bool = True,
    active: bool = True,
) -> None:
    """Seed one contest with explicit index-relevant fields."""
    contest = await make_contest(
        session,
        uberadmin,
        slug=slug,
        animator_enabled=enabled,
    )
    contest.contest_name = slug.replace("-", " ").title()
    contest.start_time = start
    contest.duration_minutes = duration_minutes
    contest.stop_answers_after = duration_minutes
    contest.active = active


async def test_list_animator_contests_filters_groups_and_orders(
    session: AsyncSession,
    uberadmin: UberAdmin,
) -> None:
    """Return only enabled, non-archived contests in operational order."""
    await _contest_at(
        session,
        uberadmin,
        slug="live-later",
        start=NOW - timedelta(minutes=10),
        duration_minutes=90,
    )
    await _contest_at(
        session,
        uberadmin,
        slug="live-sooner",
        start=NOW - timedelta(minutes=20),
        duration_minutes=40,
    )
    await _contest_at(
        session,
        uberadmin,
        slug="upcoming-later",
        start=NOW + timedelta(days=2),
        duration_minutes=60,
    )
    await _contest_at(
        session,
        uberadmin,
        slug="upcoming-sooner",
        start=NOW + timedelta(days=1),
        duration_minutes=60,
    )
    await _contest_at(
        session,
        uberadmin,
        slug="past-older",
        start=NOW - timedelta(days=4),
        duration_minutes=60,
    )
    await _contest_at(
        session,
        uberadmin,
        slug="past-newer",
        start=NOW - timedelta(days=2),
        duration_minutes=60,
    )
    await _contest_at(
        session,
        uberadmin,
        slug="animator-disabled",
        start=NOW,
        duration_minutes=60,
        enabled=False,
    )
    await _contest_at(
        session,
        uberadmin,
        slug="archived",
        start=NOW,
        duration_minutes=60,
        active=False,
    )
    await session.commit()

    groups = await list_animator_contests(session, now=NOW)

    assert [contest.login_slug for contest in groups.live] == ["live-sooner", "live-later"]
    assert [contest.login_slug for contest in groups.upcoming] == [
        "upcoming-sooner",
        "upcoming-later",
    ]
    assert [contest.login_slug for contest in groups.past] == ["past-newer", "past-older"]
    assert groups.total == 6


async def test_lifecycle_boundaries_keep_start_and_end_in_live_group(
    session: AsyncSession,
    uberadmin: UberAdmin,
) -> None:
    """Match the existing inclusive start/end contest lifecycle semantics."""
    await _contest_at(
        session,
        uberadmin,
        slug="starts-now",
        start=NOW,
        duration_minutes=60,
    )
    await _contest_at(
        session,
        uberadmin,
        slug="ends-now",
        start=NOW - timedelta(minutes=60),
        duration_minutes=60,
    )
    await session.commit()

    at_boundary = await list_animator_contests(session, now=NOW)
    after_boundary = await list_animator_contests(session, now=NOW + timedelta(microseconds=1))

    assert {contest.login_slug for contest in at_boundary.live} == {"starts-now", "ends-now"}
    assert [contest.login_slug for contest in after_boundary.past] == ["ends-now"]
    assert [contest.login_slug for contest in after_boundary.live] == ["starts-now"]
