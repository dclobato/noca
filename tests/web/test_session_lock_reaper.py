#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The reaper that releases the IP bindings of contests that have ended.

Nothing about a finished contest is wrong today -- the policy stops enforcing at
the end instant, so the binding is already inert. What this loop prevents is the
contest's *next* life: `start_time` is editable, so a re-run would enforce again
against addresses recorded at the previous sitting and refuse every team from a
seat it never sat in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import users as users_t
from shared.enumerations import RoleEnum
from web.models.contest import Contest
from web.models.users import UberAdmin, User
from web.services.session_lock_reaper import release_ended_contest_ip_locks

_VENUE_IP = "203.0.113.10"


async def _contest(session: AsyncSession, uberadmin: UberAdmin, slug: str, *, started_minutes_ago: int) -> Contest:
    """A two-hour contest whose start is `started_minutes_ago` in the past."""
    contest = Contest(
        contest_name=slug,
        contest_url=f"http://{slug}.example.com",
        login_slug=slug,
        start_time=datetime.now(UTC) - timedelta(minutes=started_minutes_ago),
        duration_minutes=120,
        stop_answers_after=120,
        stop_updating_scoreboard=120,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(contest)
    await session.flush()
    return contest


async def _bound_team(
    session: AsyncSession,
    contest: Contest,
    uberadmin: UberAdmin,
    username: str,
    *,
    locked_ip: str | None = _VENUE_IP,
    epoch: int = 3,
) -> User:
    user = User(
        username=username,
        fullname=f"Team {username}",
        role=RoleEnum.TEAM,
        contest_id=contest.id,
        created_by_uberadmin_id=uberadmin.id,
        allow_concurrent_login=False,
        locked_ip=locked_ip,
        locked_at=datetime.now(UTC) if locked_ip else None,
        session_epoch=epoch,
    )
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_an_ended_contest_gives_its_bindings_back(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The whole point: a re-run must not enforce last time's addresses."""
    ended = await _contest(session, uberadmin, "ended-contest", started_minutes_ago=300)
    team = await _bound_team(session, ended, uberadmin, "t1")

    released = await release_ended_contest_ip_locks(session)

    assert released == 1
    await session.refresh(team)
    assert team.locked_ip is None
    assert team.locked_at is None


@pytest.mark.asyncio
async def test_a_running_contest_keeps_its_bindings(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """Releasing mid-contest would hand every seat away while teams are competing."""
    team = await _bound_team(session, running_contest, uberadmin, "t1")

    released = await release_ended_contest_ip_locks(session)

    assert released == 0
    await session.refresh(team)
    assert team.locked_ip == _VENUE_IP


@pytest.mark.asyncio
async def test_the_release_does_not_supersede_anybody(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Unlike the administrator's release, this one leaves `session_epoch` alone.

    A mid-contest release must supersede the sessions bound to the old address,
    or the first of them re-binds it. After the end there is nothing to
    supersede, and bumping would sign out teams still reading their runs and the
    final scoreboard.
    """
    ended = await _contest(session, uberadmin, "ended-contest", started_minutes_ago=300)
    team = await _bound_team(session, ended, uberadmin, "t1", epoch=7)

    await release_ended_contest_ip_locks(session)

    await session.refresh(team)
    assert team.session_epoch == 7


@pytest.mark.asyncio
async def test_the_policy_flag_itself_is_left_alone(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The flag is the organiser's decision and outlives one sitting of a contest."""
    ended = await _contest(session, uberadmin, "ended-contest", started_minutes_ago=300)
    team = await _bound_team(session, ended, uberadmin, "t1")

    await release_ended_contest_ip_locks(session)

    await session.refresh(team)
    assert team.allow_concurrent_login is False


@pytest.mark.asyncio
async def test_only_the_ended_contests_are_touched(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """One cycle covers every contest, so the ones still running must be excluded."""
    ended = await _contest(session, uberadmin, "ended-contest", started_minutes_ago=300)
    live = await _bound_team(session, running_contest, uberadmin, "live")
    finished = await _bound_team(session, ended, uberadmin, "finished")

    released = await release_ended_contest_ip_locks(session)

    assert released == 1
    await session.refresh(live)
    await session.refresh(finished)
    assert live.locked_ip == _VENUE_IP
    assert finished.locked_ip is None


@pytest.mark.asyncio
async def test_a_cycle_with_nothing_bound_releases_nothing(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The common case, and it must not depend on any contest being loaded."""
    ended = await _contest(session, uberadmin, "ended-contest", started_minutes_ago=300)
    await _bound_team(session, ended, uberadmin, "t1", locked_ip=None)

    assert await release_ended_contest_ip_locks(session) == 0


@pytest.mark.asyncio
async def test_a_second_cycle_is_a_no_op(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The loop runs every half hour for the life of the process."""
    ended = await _contest(session, uberadmin, "ended-contest", started_minutes_ago=300)
    await _bound_team(session, ended, uberadmin, "t1")

    assert await release_ended_contest_ip_locks(session) == 1
    assert await release_ended_contest_ip_locks(session) == 0


@pytest.mark.asyncio
async def test_a_rescheduled_contest_starts_without_its_previous_bindings(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """The scenario the reaper exists for, played out end to end.

    A contest ends, the reaper runs, and the organiser then moves the start into
    the future for a re-run. The team must arrive unbound, free to bind wherever
    it is actually sitting.
    """
    contest = await _contest(session, uberadmin, "rerun-contest", started_minutes_ago=300)
    team = await _bound_team(session, contest, uberadmin, "t1")

    await release_ended_contest_ip_locks(session)

    contest.start_time = datetime.now(UTC) - timedelta(minutes=5)
    await session.flush()

    await session.refresh(team)
    assert contest.is_running is True
    assert team.locked_ip is None, "the re-run binds from where the team is now"
    stored = await session.scalar(select(users_t.c.locked_ip).where(users_t.c.id == team.id))
    assert stored is None
