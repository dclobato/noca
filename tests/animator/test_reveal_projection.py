#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Loader-side tests: ceremony scope and the frozen universe.

The derived views and the shared-projection equality checks live in
``test_reveal_views.py``; both modules share the fixture in ``_reveal_seed``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from animator.services import reveal_loader
from animator.services.reveal_loader import (
    UnknownSiteError,
    build_frozen_submission_ids,
    initialize_reveal_session,
)
from animator.services.reveal_projection import revealed_submissions
from shared.enumerations import Verdict
from shared.services import scoreboard_projection
from tests.animator._feed_seed import (
    START,
    make_contest,
    make_language,
    make_problem,
    make_site,
    make_user,
)
from tests.animator._reveal_seed import (
    FREEZE_MINUTES,
    add_raw_submission,
    load,
    seed_ceremony,
)
from web.models.users import UberAdmin

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Initialization and scope
# ---------------------------------------------------------------------------


async def test_global_session_initializes_unscoped(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await seed_ceremony(session, uberadmin)
    dataset = await load(session, fixture.slug, None)
    state = initialize_reveal_session(dataset)

    assert {team.id for team in dataset.teams} == {fixture.a1, fixture.a2, fixture.a3, fixture.b1}
    assert state.contest_id == fixture.contest_id
    assert (state.site_id, state.site_name, state.medal_cutoffs) == (None, None, None)
    assert state.phase == "idle"
    assert state.reveal_log == ()
    assert state.focused_team_id is None


async def test_site_session_scopes_every_projection_input(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await seed_ceremony(session, uberadmin)
    dataset = await load(session, fixture.slug, fixture.site_a)
    state = initialize_reveal_session(dataset)

    assert {team.id for team in dataset.teams} == {fixture.a1, fixture.a2, fixture.a3}
    assert all(row.team_id != fixture.b1 for row in dataset.submissions)
    assert set(dataset.judgments) == {row.id for row in dataset.submissions}
    assert state.site_id == fixture.site_a
    assert state.site_name == "Campus A"
    assert state.medal_cutoffs is not None
    assert (state.medal_cutoffs.gold, state.medal_cutoffs.silver, state.medal_cutoffs.bronze) == (1, 2, 3)


async def test_unknown_site_is_rejected(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await seed_ceremony(session, uberadmin)
    other = await make_contest(session, uberadmin, slug=f"other-{uuid.uuid4().hex[:8]}")
    foreign_site = await make_site(session, other, sitename="Elsewhere")
    await session.commit()

    with pytest.raises(UnknownSiteError):
        await load(session, fixture.slug, foreign_site.id)
    with pytest.raises(UnknownSiteError):
        await load(session, fixture.slug, "does-not-exist")


# ---------------------------------------------------------------------------
# Frozen universe: predicate and ordering
# ---------------------------------------------------------------------------


async def test_frozen_universe_uses_the_scoreboard_predicate(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await seed_ceremony(session, uberadmin)
    dataset = await load(session, fixture.slug, fixture.site_a)
    frozen = build_frozen_submission_ids(dataset)

    assert set(frozen) == {
        fixture.frozen_ac,
        fixture.frozen_pending,
        fixture.later_run,
        fixture.frozen_ce,
        fixture.frozen_pe,
    }
    assert fixture.legacy not in frozen


async def test_legacy_zero_timestamp_rows_are_pre_freeze(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await seed_ceremony(session, uberadmin)
    dataset = await load(session, fixture.slug, fixture.site_a)
    state = initialize_reveal_session(dataset)

    legacy_row = next(row for row in dataset.submissions if row.id == fixture.legacy)
    assert legacy_row.timestamp_seconds == 0
    assert fixture.legacy not in state.frozen_submission_ids
    assert any(row.id == fixture.legacy for row in revealed_submissions(dataset, state))


async def test_frozen_order_uses_timestamp_then_created_at_then_id(session: AsyncSession, uberadmin: UberAdmin) -> None:
    contest = await make_contest(
        session,
        uberadmin,
        slug=f"order-{uuid.uuid4().hex[:8]}",
        stop_updating_scoreboard=FREEZE_MINUTES,
    )
    language = await make_language(session)
    site = await make_site(session, contest, sitename="Campus A")
    team = make_user(contest, uberadmin, "t1", site_id=site.id)
    problem = make_problem(contest, 1)
    session.add_all([team, problem])
    await session.flush()

    early = START + timedelta(minutes=250)
    late = START + timedelta(minutes=251)
    rows = [
        # Same contest time and same clock: only the id separates these two.
        ("id-y", 15000, early),
        ("id-a", 15000, early),
        # Same contest time, later clock: sorts after both of the above.
        ("id-x", 15000, late),
        # Later contest time but the earliest clock: contest time still wins.
        ("id-z", 15060, START),
    ]
    for submission_id, timestamp_seconds, created_at in rows:
        await add_raw_submission(
            session,
            submission_id=submission_id,
            problem=problem,
            team=team,
            language=language,
            timestamp_seconds=timestamp_seconds,
            created_at=created_at,
            verdict=Verdict.WA,
        )
    await session.commit()

    dataset = await load(session, contest.login_slug, site.id)
    assert build_frozen_submission_ids(dataset) == ("id-a", "id-y", "id-x", "id-z")


async def test_the_universe_uses_the_shared_sort_key(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The loader must not own a second copy of the canonical ordering."""
    assert reveal_loader.submission_sort_key is scoreboard_projection.submission_sort_key

    fixture = await seed_ceremony(session, uberadmin)
    dataset = await load(session, fixture.slug, fixture.site_a)
    frozen_ids = set(build_frozen_submission_ids(dataset))
    expected = sorted(
        (row for row in dataset.submissions if row.id in frozen_ids),
        key=scoreboard_projection.submission_sort_key,
    )
    assert build_frozen_submission_ids(dataset) == tuple(row.id for row in expected)
