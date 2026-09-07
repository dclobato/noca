#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The one definition of "this team never signed in", shared by both boards."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.team_absence_status import load_absent_teams, load_teams_without_sign_in
from web.models.contest import Contest
from web.models.users import Login_History, User


def _sign_in(session: AsyncSession, user: User, when: object) -> None:
    """Record one successful sign-in for *user* at *when*."""
    session.add(Login_History(user_id=user.id, dta_login=when))


@pytest.mark.asyncio
async def test_a_team_with_no_sign_in_is_reported_absent(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    another_team_user: User,
) -> None:
    """Only the team that never signed in comes back."""
    _sign_in(session, team_user, running_contest.start_time + timedelta(minutes=5))
    await session.flush()

    absent = await load_teams_without_sign_in(session, running_contest.id, since=running_contest.start_time)

    assert absent == frozenset({another_team_user.id})


@pytest.mark.asyncio
async def test_a_sign_in_before_the_start_does_not_count(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
) -> None:
    """Warm-up presence is not presence.

    A team that opened the practice page before the contest opened and then
    walked away is exactly the no-show the marker exists for, so the window
    starts at the contest's own start instant.
    """
    _sign_in(session, team_user, running_contest.start_time - timedelta(minutes=10))
    await session.flush()

    absent = await load_teams_without_sign_in(session, running_contest.id, since=running_contest.start_time)

    assert absent == frozenset({team_user.id})


@pytest.mark.asyncio
async def test_only_teams_are_considered(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
) -> None:
    """Staff accounts are not on the board and are never reported."""
    absent = await load_teams_without_sign_in(session, running_contest.id, since=running_contest.start_time)

    assert absent == frozenset({team_user.id})
    assert judge_user.id not in absent


@pytest.mark.asyncio
async def test_another_contest_is_not_reported(
    session: AsyncSession,
    running_contest: Contest,
    stopped_contest: Contest,
    team_user: User,
) -> None:
    """The lookup is scoped to the contest asked about."""
    absent = await load_teams_without_sign_in(session, stopped_contest.id, since=stopped_contest.start_time)

    assert absent == frozenset()
    assert team_user.id not in absent


# ---------------------------------------------------------------------------
# The presence half (#219)
# ---------------------------------------------------------------------------


class _FakePresence:
    """A Valkey stand-in whose live keys are exactly the ids it was given."""

    def __init__(self, online_ids: set[str], *, fail: bool = False) -> None:
        self._online = online_ids
        self._fail = fail
        self.requested: list[str] = []

    async def mget(self, keys: list[str]) -> list[str | None]:
        if self._fail:
            raise RuntimeError("valkey is unreachable")
        self.requested = list(keys)
        return ["1" if any(key.endswith(f":{user_id}") for user_id in self._online) else None for key in keys]


@pytest.mark.asyncio
async def test_a_team_present_since_before_the_start_is_not_absent(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
) -> None:
    """The #219 report: logged in during warm-up, working, marked as a no-show.

    `Start contest now` moves the start to the present instant, so the team's
    warm-up sign-in falls outside the window. Only presence can tell that seat
    apart from an empty one.
    """
    _sign_in(session, team_user, running_contest.start_time - timedelta(minutes=5))
    await session.flush()

    without_presence = await load_absent_teams(session, running_contest.id, since=running_contest.start_time)
    with_presence = await load_absent_teams(
        session,
        running_contest.id,
        since=running_contest.start_time,
        valkey=_FakePresence({team_user.id}),
    )

    assert team_user.id in without_presence, "the sign-in window alone still reports the bug"
    assert with_presence == frozenset(), "presence clears it"


@pytest.mark.asyncio
async def test_a_team_that_walked_away_after_warm_up_is_still_absent(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
) -> None:
    """The case the window exists for, and the reason it is kept beside presence.

    Dropping the window instead of adding presence would have traded the false
    positive above for this false negative, which is the one the feature was
    built to catch.
    """
    _sign_in(session, team_user, running_contest.start_time - timedelta(minutes=5))
    await session.flush()

    absent = await load_absent_teams(
        session,
        running_contest.id,
        since=running_contest.start_time,
        valkey=_FakePresence(set()),
    )

    assert absent == frozenset({team_user.id})


@pytest.mark.asyncio
async def test_presence_never_adds_a_team_to_the_answer(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
) -> None:
    """A team that signed in since the start is never asked about, let alone marked."""
    _sign_in(session, team_user, running_contest.start_time + timedelta(minutes=1))
    await session.flush()
    presence = _FakePresence(set())

    absent = await load_absent_teams(session, running_contest.id, since=running_contest.start_time, valkey=presence)

    assert absent == frozenset()
    assert presence.requested == [], "no round trip for a team the window already cleared"


@pytest.mark.asyncio
async def test_a_valkey_outage_falls_back_to_the_sign_in_window(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
) -> None:
    """The failure direction is deliberate: today's behaviour, not a screen of alarms."""
    _sign_in(session, team_user, running_contest.start_time - timedelta(minutes=5))
    await session.flush()

    absent = await load_absent_teams(
        session,
        running_contest.id,
        since=running_contest.start_time,
        valkey=_FakePresence({team_user.id}, fail=True),
    )

    assert absent == frozenset({team_user.id})


@pytest.mark.asyncio
async def test_omitting_valkey_leaves_the_window_alone(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
) -> None:
    """A deployment with presence disabled behaves exactly as it did before."""
    absent_without = await load_absent_teams(session, running_contest.id, since=running_contest.start_time)
    absent_legacy = await load_teams_without_sign_in(session, running_contest.id, since=running_contest.start_time)

    assert absent_without == absent_legacy == frozenset({team_user.id})
