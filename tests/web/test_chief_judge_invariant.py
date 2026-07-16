#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""A contest with judges must always have a chief judge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from web.models.contest import Contest
from web.models.users import UberAdmin, User
from web.services.contest_service import ChiefJudgeInvariantError
from web.services.contest_user_service import batch_import_users, create_user, remove_user, update_user
from web.services.site_service import normalize_site_name_key

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def scheduled_contest(session: AsyncSession, uberadmin: UberAdmin) -> Contest:
    """A contest that has not started yet, so its users may still be removed."""
    contest = Contest(
        contest_name="Scheduled Contest",
        contest_url="http://scheduled.example.com",
        login_slug="scheduled-contest",
        start_time=datetime.now(UTC) + timedelta(hours=2),
        duration_minutes=120,
        stop_answers_after=120,
        stop_updating_scoreboard=120,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(contest)
    await session.flush()
    return contest


async def _add_judge(
    session: AsyncSession,
    contest: Contest,
    uberadmin: UberAdmin,
    username: str,
) -> User:
    judge, _ = await create_user(
        session,
        contest,
        uberadmin,
        username=username,
        fullname=username.replace("-", " ").title(),
        role=RoleEnum.JUDGE,
        password="TestPass1!",
    )
    return judge


async def _team_site_id(session: AsyncSession, contest: Contest) -> str:
    from web.models.site import Site

    site = Site(
        sitename="Main Site",
        sitename_normalized=normalize_site_name_key("Main Site"),
        contest_id=contest.id,
    )
    session.add(site)
    await session.flush()
    return site.id


async def test_the_first_judge_becomes_the_chief_judge(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    judge = await _add_judge(session, running_contest, uberadmin, "judge-one")

    assert running_contest.chief_judge_id == judge.id


async def test_a_second_judge_leaves_the_chief_judge_untouched(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    first = await _add_judge(session, running_contest, uberadmin, "judge-one")
    await _add_judge(session, running_contest, uberadmin, "judge-two")

    assert running_contest.chief_judge_id == first.id


async def test_demoting_the_chief_judge_promotes_the_only_remaining_judge(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    chief = await _add_judge(session, running_contest, uberadmin, "judge-one")
    successor = await _add_judge(session, running_contest, uberadmin, "judge-two")
    site_id = await _team_site_id(session, running_contest)

    await update_user(
        session,
        running_contest,
        chief,
        fullname=chief.fullname,
        role=RoleEnum.STAFF,
        site_id=site_id,
    )

    assert running_contest.chief_judge_id == successor.id


async def test_demoting_the_chief_judge_is_blocked_when_two_judges_remain(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    chief = await _add_judge(session, running_contest, uberadmin, "judge-one")
    await _add_judge(session, running_contest, uberadmin, "judge-two")
    await _add_judge(session, running_contest, uberadmin, "judge-three")
    site_id = await _team_site_id(session, running_contest)
    chief_id = chief.id

    with pytest.raises(ChiefJudgeInvariantError):
        await update_user(
            session,
            running_contest,
            chief,
            fullname=chief.fullname,
            role=RoleEnum.STAFF,
            site_id=site_id,
        )

    await session.rollback()
    await session.refresh(chief)
    await session.refresh(running_contest)
    assert chief.role is RoleEnum.JUDGE
    assert running_contest.chief_judge_id == chief_id


async def test_demoting_the_last_judge_clears_the_chief_judge(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    chief = await _add_judge(session, running_contest, uberadmin, "judge-one")
    site_id = await _team_site_id(session, running_contest)

    await update_user(
        session,
        running_contest,
        chief,
        fullname=chief.fullname,
        role=RoleEnum.STAFF,
        site_id=site_id,
    )

    assert running_contest.chief_judge_id is None


async def test_deleting_the_chief_judge_promotes_the_only_remaining_judge(
    session: AsyncSession,
    scheduled_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    chief = await _add_judge(session, scheduled_contest, uberadmin, "judge-one")
    successor = await _add_judge(session, scheduled_contest, uberadmin, "judge-two")

    await remove_user(session, scheduled_contest, chief)

    assert scheduled_contest.chief_judge_id == successor.id


async def test_deleting_the_chief_judge_is_blocked_when_two_judges_remain(
    session: AsyncSession,
    scheduled_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    chief = await _add_judge(session, scheduled_contest, uberadmin, "judge-one")
    await _add_judge(session, scheduled_contest, uberadmin, "judge-two")
    await _add_judge(session, scheduled_contest, uberadmin, "judge-three")
    chief_id = chief.id

    with pytest.raises(ChiefJudgeInvariantError):
        await remove_user(session, scheduled_contest, chief)

    await session.rollback()
    await session.refresh(scheduled_contest)
    assert scheduled_contest.chief_judge_id == chief_id
    assert await session.get(User, chief_id) is not None


async def test_an_import_row_demoting_the_chief_judge_fails_without_blocking_the_others(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    chief = await _add_judge(session, running_contest, uberadmin, "judge-one")
    await _add_judge(session, running_contest, uberadmin, "judge-two")
    await _add_judge(session, running_contest, uberadmin, "judge-three")
    chief_id = chief.id

    result = await batch_import_users(
        session,
        running_contest,
        uberadmin,
        [
            {"username": chief.username, "fullname": chief.fullname, "role": "staff", "site": "Campus A"},
            {"username": "team01", "fullname": "Team 01", "role": "team", "site": "Campus A"},
        ],
    )

    assert result.failed == 1
    assert result.created == 1
    demoted_row = next(row for row in result.results if row.username == "judge-one")
    assert demoted_row.status == "failed"
    assert "chief judge" in demoted_row.detail

    await session.refresh(running_contest)
    assert running_contest.chief_judge_id == chief_id
    assert (await session.get(User, chief_id)).role is RoleEnum.JUDGE


async def test_deleting_the_last_judge_clears_the_chief_judge(
    session: AsyncSession,
    scheduled_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    chief = await _add_judge(session, scheduled_contest, uberadmin, "judge-one")

    await remove_user(session, scheduled_contest, chief)

    assert scheduled_contest.chief_judge_id is None
