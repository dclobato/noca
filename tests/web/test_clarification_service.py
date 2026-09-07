#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Integration/behavioral tests for clarification_service.py.

Each test function maps to one Gherkin scenario from the feature specification.
Tests run against a fresh in-memory SQLite database (see conftest.py).

Note on concurrency: SQLite ignores SELECT FOR UPDATE, but the service uses
an atomic UPDATE ... WHERE judge_id IS NULL pattern which SQLite does enforce.
Sequential calls in the same test correctly verify the concurrency guard.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
import valkey.asyncio as aivalkey
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import ProblemValidatorType, RoleEnum
from shared.services.lock_service import get_lock
from web.models.clarification import Clarification
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.users import UberAdmin, User
from web.services.assorted_utils import minutes_from_contest_start
from web.services.chief_judge_permissions import is_chief_judge
from web.services.clarification_reaper import AUTO_ANSWER_PLACEHOLDER, conclude_finished_contest_clarifications
from web.services.clarification_service import (
    ClarificationAlreadyAcquiredError,
    ClarificationAlreadyAnsweredError,
    ClarificationHiddenError,
    ClarificationNotAcquiredByActorError,
    ContestNotRunningError,
    ForbiddenClarificationActionError,
    can_create_announcement,
    can_request_clarification,
    create_announcement,
    create_clarification,
    get_clarification,
    toggle_hidden_clarification,
)
from web.services.clarification_service import (
    acquire_clarification as _acquire_clarification,
)
from web.services.clarification_service import (
    answer_clarification as _answer_clarification,
)
from web.services.clarification_service import (
    list_clarifications as _list_clarifications,
)
from web.services.clarification_service import (
    release_clarification as _release_clarification,
)

_LOCK_CLIENT: aivalkey.Valkey | None = None


@pytest_asyncio.fixture(autouse=True)
async def _install_lock_client(valkey_client: aivalkey.Valkey) -> None:
    global _LOCK_CLIENT
    _LOCK_CLIENT = valkey_client


async def list_clarifications(session: AsyncSession, contest: Contest, actor: User | UberAdmin):
    assert _LOCK_CLIENT is not None
    views, _available = await _list_clarifications(session, contest, actor, _LOCK_CLIENT)
    return views


async def acquire_clarification(session: AsyncSession, contest: Contest, actor: User, clarification: Clarification):
    assert _LOCK_CLIENT is not None
    return await _acquire_clarification(session, contest, actor, clarification, _LOCK_CLIENT)


async def release_clarification(
    session: AsyncSession, contest: Contest, actor: User | UberAdmin, clarification: Clarification
):
    assert _LOCK_CLIENT is not None
    return await _release_clarification(session, contest, actor, clarification, _LOCK_CLIENT)


async def answer_clarification(
    session: AsyncSession,
    contest: Contest,
    actor: User,
    clarification: Clarification,
    *,
    answer: str,
    is_contest_public: bool,
):
    assert _LOCK_CLIENT is not None
    return await _answer_clarification(
        session,
        contest,
        actor,
        clarification,
        _LOCK_CLIENT,
        answer=answer,
        is_contest_public=is_contest_public,
    )


# ---------------------------------------------------------------------------
# Scenario 1: Team creates a clarification
# ---------------------------------------------------------------------------


async def test_team_creates_clarification_visible_to_own_team(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    another_team_user: User,
    contest_problem: Problem,
) -> None:
    """
    Scenario: Team creates a clarification
        When "Team A" requests a clarification with question "Is N <= 100?"
        Then the clarification is saved with "answered_at" as Null
        And the clarification is visible to "Team A"
        And the clarification is NOT visible to "Team B"
    """
    clari = await create_clarification(
        session,
        running_contest,
        team_user,
        problem_id=contest_problem.id,
        question="Is N <= 100?",
    )

    assert clari.answered_at is None
    assert clari.created_at is not None
    assert clari.team_id == team_user.id
    assert minutes_from_contest_start(running_contest.start_time, clari.created_at) >= 30

    visible_to_owner = await list_clarifications(session, running_contest, team_user)
    assert any(v.id == clari.id for v in visible_to_owner)

    visible_to_other = await list_clarifications(session, running_contest, another_team_user)
    assert not any(v.id == clari.id for v in visible_to_other)


async def test_non_team_role_cannot_create_clarification(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    with pytest.raises(ForbiddenClarificationActionError):
        await create_clarification(
            session,
            running_contest,
            judge_user,
            problem_id=contest_problem.id,
            question="Can a judge ask?",
        )


async def test_cannot_create_clarification_when_contest_not_running(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    from shared.enumerations import RoleEnum

    problem = Problem(
        contest_id=stopped_contest.id,
        title="Old Problem",
        ordinal=1,
        color="#aaaaaa",
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(problem)
    team = User(
        username="stopped_team",
        fullname="Stopped Team",
        role=RoleEnum.TEAM,
        contest_id=stopped_contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    team.password = "TestPass1!"
    session.add(team)
    await session.flush()

    with pytest.raises(ContestNotRunningError):
        await create_clarification(
            session,
            stopped_contest,
            team,
            problem_id=problem.id,
            question="Is the contest still running?",
        )


# ---------------------------------------------------------------------------
# Scenario 2: Judge acquires a clarification (Concurrency)
# ---------------------------------------------------------------------------


async def test_judge_acquires_clarification_successfully(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Time limit?"
    )

    acquired = await acquire_clarification(session, running_contest, judge_user, clari)

    assert acquired.judge_id is None
    assert _LOCK_CLIENT is not None
    lock = await get_lock(
        _LOCK_CLIENT,
        kind="clarification",
        contest_id=running_contest.id,
        resource_id=clari.id,
    )
    assert lock is not None
    assert lock.holder_id == judge_user.id


async def test_acquire_persists_acquisition_time_and_survives_answer(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    """The acquisition instant is durable, so an answered clarification reports a real service time.

    Mirrors the task-service regression: the acquisition instant used to live
    only on the Valkey lock, which `answer_clarification` releases before the
    row can ever be read back.
    """
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Service time?"
    )
    assert clari.acquired_at is None

    acquired = await acquire_clarification(session, running_contest, judge_user, clari)
    assert acquired.acquired_at is not None
    assert acquired.acquired_timestamp_seconds is not None

    answered = await answer_clarification(
        session, running_contest, judge_user, clari, answer="42", is_contest_public=False
    )
    assert answered.answered_at is not None
    assert answered.acquired_at is not None
    assert answered.acquired_at <= answered.answered_at


async def test_second_judge_cannot_acquire_already_acquired_clarification(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    another_judge_user: User,
    contest_problem: Problem,
) -> None:
    """
    Scenario: Judge acquires a clarification (Concurrency)
        When "Judge X" and "Judge Y" try to acquire the clarification simultaneously
        Then one Judge receives a success response with the lock
        And the other Judge receives an Exception

    The sequential test correctly verifies the guard: after Judge X acquires,
    the UPDATE WHERE judge_id IS NULL finds 0 rows for Judge Y's attempt.
    """
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Concurrent?"
    )
    await acquire_clarification(session, running_contest, judge_user, clari)

    with pytest.raises(ClarificationAlreadyAcquiredError):
        await acquire_clarification(session, running_contest, another_judge_user, clari)


async def test_zero_clarification_timeout_locks_until_contest_end(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    running_contest.clarifications_timeout_minutes = 0
    await session.flush()

    clari = await create_clarification(
        session,
        running_contest,
        team_user,
        problem_id=contest_problem.id,
        question="Lock until contest end?",
    )
    await acquire_clarification(session, running_contest, judge_user, clari)

    assert _LOCK_CLIENT is not None
    lock = await get_lock(
        _LOCK_CLIENT,
        kind="clarification",
        contest_id=running_contest.id,
        resource_id=clari.id,
    )
    assert lock is not None
    assert abs((lock.expires_at - running_contest.end_time).total_seconds()) <= 2


async def test_cannot_acquire_when_contest_not_running(
    session: AsyncSession,
    running_contest: Contest,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    # Create a clarification in the running contest then pass the stopped contest
    # to acquire — simulates what happens when the contest ends mid-session.
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Q?"
    )

    with pytest.raises(ContestNotRunningError):
        await acquire_clarification(session, stopped_contest, judge_user, clari)


async def test_cannot_acquire_hidden_clarification(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    admin_user: User,
    contest_problem: Problem,
) -> None:
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Hidden?"
    )
    await toggle_hidden_clarification(session, admin_user, clari)

    with pytest.raises(ClarificationHiddenError):
        await acquire_clarification(session, running_contest, judge_user, clari)


async def test_cannot_acquire_already_answered_clarification(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    another_judge_user: User,
    contest_problem: Problem,
) -> None:
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Answered?"
    )
    await acquire_clarification(session, running_contest, judge_user, clari)
    await answer_clarification(session, running_contest, judge_user, clari, answer="Yes.", is_contest_public=False)

    with pytest.raises(ClarificationAlreadyAnsweredError):
        await acquire_clarification(session, running_contest, another_judge_user, clari)


# ---------------------------------------------------------------------------
# Scenario 3: Judge answers a clarification successfully
# ---------------------------------------------------------------------------


async def test_judge_answers_clarification_successfully(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    another_team_user: User,
    judge_user: User,
    admin_user: User,
    contest_problem: Problem,
) -> None:
    """
    Scenario: Judge answers a clarification successfully
        Given "Judge X" has acquired a clarification
        And the acquisition time is within "Contest.clarifications_timeout_minutes"
        When "Judge X" submits the answer "Yes" with is_contest_public=True
        Then the clarification "judge_id" remains "Judge X"
        And "answered_at" is updated to the current timestamp
        And "Team A" can see the clarification
        And "Team B" can now see the clarification
    """
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Memory limit?"
    )
    await acquire_clarification(session, running_contest, judge_user, clari)

    answered = await answer_clarification(
        session,
        running_contest,
        judge_user,
        clari,
        answer="256 MB",
        is_contest_public=True,
    )

    assert answered.answered_at is not None
    assert answered.answer == "256 MB"
    assert answered.is_contest_public is True
    assert answered.judge_id == judge_user.id  # lock is NOT cleared on answer

    # Team A (requester) can still see it
    team_a_view = await list_clarifications(session, running_contest, team_user)
    assert any(v.id == clari.id for v in team_a_view)

    # Team B can now see it because is_contest_public=True
    team_b_view = await list_clarifications(session, running_contest, another_team_user)
    assert any(v.id == clari.id for v in team_b_view)

    # Judge sees it but judge_id is redacted
    judge_view = await list_clarifications(session, running_contest, judge_user)
    judge_entry = next(v for v in judge_view if v.id == clari.id)
    assert judge_entry.judge_id is None

    # Admin sees it with judge_id populated
    admin_view = await list_clarifications(session, running_contest, admin_user)
    admin_entry = next(v for v in admin_view if v.id == clari.id)
    assert admin_entry.judge_id == judge_user.id


async def test_judge_cannot_answer_without_acquiring(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Q?"
    )

    with pytest.raises(ClarificationNotAcquiredByActorError):
        await answer_clarification(session, running_contest, judge_user, clari, answer="A.", is_contest_public=False)


async def test_answer_is_immutable(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Q?"
    )
    await acquire_clarification(session, running_contest, judge_user, clari)
    await answer_clarification(
        session, running_contest, judge_user, clari, answer="First answer.", is_contest_public=False
    )

    with pytest.raises(ClarificationAlreadyAnsweredError):
        await answer_clarification(
            session, running_contest, judge_user, clari, answer="Second answer.", is_contest_public=False
        )


# ---------------------------------------------------------------------------
# Scenario 4: Judge tries to answer after timeout
# ---------------------------------------------------------------------------


async def test_answered_clarification_releases_active_lock(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Time limit?"
    )
    await acquire_clarification(session, running_contest, judge_user, clari)

    await answer_clarification(
        session, running_contest, judge_user, clari, answer="2 seconds.", is_contest_public=False
    )

    assert clari.judge_id == judge_user.id
    assert _LOCK_CLIENT is not None
    assert (
        await get_lock(
            _LOCK_CLIENT,
            kind="clarification",
            contest_id=running_contest.id,
            resource_id=clari.id,
        )
        is None
    )
    views = await list_clarifications(session, running_contest, judge_user)
    answered_view = next(view for view in views if view.id == clari.id)
    assert answered_view.acquired_at is None


# ---------------------------------------------------------------------------
# Scenario 5: Moderation — Hiding offensive content
# ---------------------------------------------------------------------------


async def test_hidden_clarification_invisible_to_team_blocks_answer_visible_to_judge(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    admin_user: User,
    contest_problem: Problem,
) -> None:
    """
    Scenario: Moderation - Hiding offensive content
        Given "Team A" submits a clarification with offensive text
        When an "Admin" calls the hide endpoint
        Then the clarification "is_hidden" becomes True
        And "Team A" can no longer see the clarification in their list
        And "Judge X" cannot submit an answer to this clarification
    """
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Offensive!"
    )
    await acquire_clarification(session, running_contest, judge_user, clari)

    await toggle_hidden_clarification(session, admin_user, clari)

    assert clari.hidden is True
    assert clari.hidden_by_admin_id == admin_user.id
    assert clari.hidden_by_judge_id is None
    assert clari.hidden_at is not None

    # Team A can no longer see it
    team_view = await list_clarifications(session, running_contest, team_user)
    assert not any(v.id == clari.id for v in team_view)

    # Judge cannot answer it
    with pytest.raises(ClarificationHiddenError):
        await answer_clarification(session, running_contest, judge_user, clari, answer="N/A", is_contest_public=False)

    # Judge can still see it in their list
    judge_view = await list_clarifications(session, running_contest, judge_user)
    assert any(v.id == clari.id for v in judge_view)


async def test_unhide_restores_visibility(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    admin_user: User,
    contest_problem: Problem,
) -> None:
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Can I see this?"
    )

    # Hide it
    await toggle_hidden_clarification(session, admin_user, clari)
    assert clari.hidden is True

    # Unhide it
    await toggle_hidden_clarification(session, admin_user, clari)
    assert clari.hidden is False
    assert clari.hidden_at is None
    assert clari.hidden_by_admin_id is None
    assert clari.hidden_by_judge_id is None

    # Team can see it again
    team_view = await list_clarifications(session, running_contest, team_user)
    assert any(v.id == clari.id for v in team_view)


async def test_judge_can_hide_clarification(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Off-topic."
    )

    await toggle_hidden_clarification(session, judge_user, clari)

    assert clari.hidden is True
    assert clari.hidden_by_judge_id == judge_user.id
    assert clari.hidden_by_admin_id is None


# ---------------------------------------------------------------------------
# Release tests
# ---------------------------------------------------------------------------


async def test_judge_can_release_own_lock(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Release me."
    )
    await acquire_clarification(session, running_contest, judge_user, clari)
    assert clari.judge_id is None

    await release_clarification(session, running_contest, judge_user, clari)

    assert clari.judge_id is None
    assert _LOCK_CLIENT is not None
    assert (
        await get_lock(
            _LOCK_CLIENT,
            kind="clarification",
            contest_id=running_contest.id,
            resource_id=clari.id,
        )
        is None
    )


async def test_judge_cannot_release_another_judges_lock(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    another_judge_user: User,
    contest_problem: Problem,
) -> None:
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Whose lock?"
    )
    await acquire_clarification(session, running_contest, judge_user, clari)

    with pytest.raises(ClarificationNotAcquiredByActorError):
        await release_clarification(session, running_contest, another_judge_user, clari)


async def test_admin_can_release_any_judges_lock(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    admin_user: User,
    contest_problem: Problem,
) -> None:
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Admin release?"
    )
    await acquire_clarification(session, running_contest, judge_user, clari)

    await release_clarification(session, running_contest, admin_user, clari)

    assert clari.judge_id is None


async def test_reaper_auto_answers_open_clarification_for_past_contest(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    stopped_problem = Problem(
        contest_id=stopped_contest.id,
        title="Stopped Problem",
        ordinal=1,
        color="#00aa00",
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(stopped_problem)
    await session.flush()
    owner = User(
        username="owner_clari_reaper",
        fullname="Owner Clari Reaper",
        role=RoleEnum.ADMIN,
        contest_id=stopped_contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    owner.password = "TestPass1!"
    session.add(owner)
    await session.flush()
    stopped_contest.owner_user_id = owner.id
    stopped_team = User(
        username="team_clari_reaper",
        fullname="Team Clari Reaper",
        role=RoleEnum.TEAM,
        contest_id=stopped_contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    stopped_team.password = "TestPass1!"
    session.add(stopped_team)
    await session.flush()
    clari = Clarification(
        team_id=stopped_team.id,
        problem_id=stopped_problem.id,
        question="Reap me.",
        created_timestamp_seconds=minutes_from_contest_start(stopped_contest.start_time, stopped_contest.end_time) * 60,
        acquired_at=datetime.now(UTC) - timedelta(hours=3),
        acquired_timestamp_seconds=120,
    )
    session.add(clari)
    await session.flush()

    concluded = await conclude_finished_contest_clarifications(session)

    assert concluded == 1
    assert clari.judge_id == owner.id
    assert clari.answer == AUTO_ANSWER_PLACEHOLDER
    # An administrative close is not a handled service: a clarification
    # abandoned hours earlier must not report a multi-hour service time.
    assert clari.acquired_at is None
    assert clari.acquired_timestamp_seconds is None
    assert clari.answered_at is not None


async def test_reaper_ignores_answered_clarification(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Answered already?"
    )
    await acquire_clarification(session, running_contest, judge_user, clari)
    await answer_clarification(session, running_contest, judge_user, clari, answer="Yes.", is_contest_public=False)

    released = await conclude_finished_contest_clarifications(session)

    assert released == 0
    assert clari.judge_id == judge_user.id


async def test_reaper_ignores_contest_without_timeout(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    contest_problem: Problem,
) -> None:
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="No timeout."
    )
    await acquire_clarification(session, running_contest, judge_user, clari)

    released = await conclude_finished_contest_clarifications(session)

    assert released == 0
    assert clari.judge_id is None


# ---------------------------------------------------------------------------
# get_clarification
# ---------------------------------------------------------------------------


async def test_get_clarification_returns_none_for_wrong_contest(
    session: AsyncSession,
    running_contest: Contest,
    stopped_contest: Contest,
    team_user: User,
    contest_problem: Problem,
) -> None:
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Scoped?"
    )

    result = await get_clarification(session, stopped_contest, clari.id)
    assert result is None

    result = await get_clarification(session, running_contest, clari.id)
    assert result is not None
    assert result.id == clari.id


# ---------------------------------------------------------------------------
# Judge identity visibility in ClarificationView
# ---------------------------------------------------------------------------


async def test_judge_identity_redacted_for_non_admin_viewers(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    admin_user: User,
    another_team_user: User,
    contest_problem: Problem,
) -> None:
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Who answered?"
    )
    await acquire_clarification(session, running_contest, judge_user, clari)
    await answer_clarification(session, running_contest, judge_user, clari, answer="I did.", is_contest_public=True)

    # Admin sees judge_id
    admin_view = await list_clarifications(session, running_contest, admin_user)
    admin_entry = next(v for v in admin_view if v.id == clari.id)
    assert admin_entry.judge_id == judge_user.id

    # Judge does NOT see judge_id (redacted)
    judge_view = await list_clarifications(session, running_contest, judge_user)
    judge_entry = next(v for v in judge_view if v.id == clari.id)
    assert judge_entry.judge_id is None

    # Team does NOT see judge_id (redacted)
    team_view = await list_clarifications(session, running_contest, team_user)
    team_entry = next(v for v in team_view if v.id == clari.id)
    assert team_entry.judge_id is None


async def test_admin_can_acquire_and_answer_a_clarification(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    admin_user: User,
    contest_problem: Problem,
) -> None:
    clari = await create_clarification(
        session,
        running_contest,
        team_user,
        problem_id=contest_problem.id,
        question="Is the input sorted?",
    )

    await acquire_clarification(session, running_contest, admin_user, clari)
    answered = await answer_clarification(
        session,
        running_contest,
        admin_user,
        clari,
        answer="No.",
        is_contest_public=True,
    )

    assert answered.judge_id == admin_user.id
    assert answered.answer == "No."
    assert answered.answered_at is not None


async def test_team_and_staff_cannot_acquire_or_answer_a_clarification(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    uberadmin: UberAdmin,
    contest_problem: Problem,
) -> None:
    staff_user = User(
        username="clari_staff",
        fullname="Clarification Staff",
        role=RoleEnum.STAFF,
        contest_id=running_contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    staff_user.password = "TestPass1!"
    session.add(staff_user)
    await session.flush()

    clari = await create_clarification(
        session,
        running_contest,
        team_user,
        problem_id=contest_problem.id,
        question="Can I ask myself?",
    )

    for actor in (team_user, staff_user):
        with pytest.raises(ForbiddenClarificationActionError):
            await acquire_clarification(session, running_contest, actor, clari)

        with pytest.raises(ForbiddenClarificationActionError):
            await answer_clarification(
                session,
                running_contest,
                actor,
                clari,
                answer="nope",
                is_contest_public=False,
            )


# ---------------------------------------------------------------------------
# General (problem-less) clarifications
# ---------------------------------------------------------------------------


async def test_team_creates_general_clarification_without_a_problem(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    another_team_user: User,
    judge_user: User,
    admin_user: User,
) -> None:
    """A clarification with no problem is scoped to the contest through its author."""
    clari = await create_clarification(
        session,
        running_contest,
        team_user,
        problem_id=None,
        question="Where is the printer?",
    )

    assert clari.problem_id is None

    owner_view = await list_clarifications(session, running_contest, team_user)
    assert any(v.id == clari.id and v.problem_id is None for v in owner_view)

    for staff in (judge_user, admin_user):
        staff_view = await list_clarifications(session, running_contest, staff)
        assert any(v.id == clari.id for v in staff_view)

    other_team_view = await list_clarifications(session, running_contest, another_team_user)
    assert not any(v.id == clari.id for v in other_team_view)

    assert await get_clarification(session, running_contest, clari.id) is clari


async def test_general_announcement_is_public_to_every_team(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    team_user: User,
    another_team_user: User,
) -> None:
    announcement = await create_announcement(
        session,
        running_contest,
        judge_user,
        problem_id=None,
        announcement="The network will be restarted in five minutes.",
    )

    assert announcement.problem_id is None
    assert announcement.is_contest_public is True

    for team in (team_user, another_team_user):
        view = await list_clarifications(session, running_contest, team)
        assert any(v.id == announcement.id for v in view)


async def test_problem_sort_keeps_general_clarifications_grouped_last(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    admin_user: User,
    contest_problem: Problem,
) -> None:
    assert _LOCK_CLIENT is not None
    general = await create_clarification(
        session, running_contest, team_user, problem_id=None, question="General question?"
    )
    on_problem = await create_clarification(
        session, running_contest, team_user, problem_id=contest_problem.id, question="Problem question?"
    )

    for sort_by in ("problem_asc", "problem_desc"):
        views, _available = await _list_clarifications(session, running_contest, admin_user, _LOCK_CLIENT, sort_by)
        ids = [v.id for v in views]
        assert set(ids) == {general.id, on_problem.id}
        assert ids[-1] == general.id


async def test_general_clarification_supports_the_full_lifecycle(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    admin_user: User,
) -> None:
    clari = await create_clarification(
        session, running_contest, team_user, problem_id=None, question="Can we use the whiteboard?"
    )

    await acquire_clarification(session, running_contest, judge_user, clari)
    answered = await answer_clarification(
        session,
        running_contest,
        judge_user,
        clari,
        answer="Yes.",
        is_contest_public=True,
    )
    assert answered.answer == "Yes."

    hidden = await toggle_hidden_clarification(session, admin_user, clari)
    assert hidden.hidden is True
    assert hidden.hidden_by_admin_id == admin_user.id
    assert hidden.hidden_timestamp_seconds is not None

    unhidden = await toggle_hidden_clarification(session, admin_user, clari)
    assert unhidden.hidden is False


async def test_reaper_auto_answers_general_clarification_for_past_contest(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    owner = User(
        username="owner_general_reaper",
        fullname="Owner General Reaper",
        role=RoleEnum.ADMIN,
        contest_id=stopped_contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    owner.password = "TestPass1!"
    session.add(owner)
    await session.flush()
    stopped_contest.owner_user_id = owner.id

    stopped_team = User(
        username="team_general_reaper",
        fullname="Team General Reaper",
        role=RoleEnum.TEAM,
        contest_id=stopped_contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    stopped_team.password = "TestPass1!"
    session.add(stopped_team)
    await session.flush()

    clari = Clarification(
        team_id=stopped_team.id,
        problem_id=None,
        question="Reap this general one.",
        created_timestamp_seconds=0,
    )
    session.add(clari)
    await session.flush()

    concluded = await conclude_finished_contest_clarifications(session)

    assert concluded == 1
    assert clari.answer == AUTO_ANSWER_PLACEHOLDER
    assert clari.judge_id == owner.id


# ---------------------------------------------------------------------------
# Scenario: announcements outside the running window
#
# Contest lifecycle state derives purely from `start_time`, so these tests shift
# the `running_contest` fixture instead of adding fixtures; that keeps the
# `judge_user` / `another_judge_user` / `admin_user` fixtures (all bound to that
# contest) usable.
# ---------------------------------------------------------------------------


def _make_upcoming(contest: Contest) -> None:
    """Move the contest's start into the future, before it has begun."""
    contest.start_time = datetime.now(UTC) + timedelta(hours=1)
    assert contest.upcoming


def _make_past(contest: Contest) -> None:
    """Move the contest's start far enough back that it has already ended."""
    contest.start_time = datetime.now(UTC) - timedelta(hours=5)
    assert contest.is_past


@pytest.mark.parametrize("shift", (_make_upcoming, _make_past))
async def test_admin_announces_outside_the_running_window(
    session: AsyncSession,
    running_contest: Contest,
    admin_user: User,
    team_user: User,
    shift,
) -> None:
    shift(running_contest)

    announcement = await create_announcement(
        session,
        running_contest,
        admin_user,
        problem_id=None,
        announcement="The venue opens one hour before the contest.",
    )

    assert announcement.is_contest_public is True
    assert announcement.answered_at is not None
    # `compute_timestamp_seconds` clamps to zero, so a pre-start announcement is
    # recorded at contest minute zero rather than at a negative offset.
    assert announcement.created_timestamp_seconds >= 0

    view = await list_clarifications(session, running_contest, team_user)
    assert any(v.id == announcement.id for v in view)


@pytest.mark.parametrize("shift", (_make_upcoming, _make_past))
async def test_chief_judge_announces_outside_the_running_window(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    shift,
) -> None:
    running_contest.chief_judge_id = judge_user.id
    await session.flush()
    shift(running_contest)

    announcement = await create_announcement(
        session,
        running_contest,
        judge_user,
        problem_id=None,
        announcement="Bring your printed team list.",
    )

    assert announcement.judge_id == judge_user.id
    assert announcement.is_contest_public is True


@pytest.mark.parametrize("shift", (_make_upcoming, _make_past))
async def test_plain_judge_cannot_announce_outside_the_running_window(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    another_judge_user: User,
    shift,
) -> None:
    running_contest.chief_judge_id = judge_user.id
    await session.flush()
    shift(running_contest)

    with pytest.raises(ContestNotRunningError):
        await create_announcement(
            session,
            running_contest,
            another_judge_user,
            problem_id=None,
            announcement="Not my call to make.",
        )


async def test_plain_judge_still_announces_while_the_contest_runs(
    session: AsyncSession,
    running_contest: Contest,
    another_judge_user: User,
) -> None:
    announcement = await create_announcement(
        session,
        running_contest,
        another_judge_user,
        problem_id=None,
        announcement="Problem C has a clarified constraint.",
    )

    assert announcement.judge_id == another_judge_user.id


async def test_team_cannot_announce_before_the_contest_starts(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
) -> None:
    _make_upcoming(running_contest)

    # The role check precedes the lifecycle check, so a team is refused as
    # forbidden rather than as "contest not running".
    with pytest.raises(ForbiddenClarificationActionError):
        await create_announcement(
            session,
            running_contest,
            team_user,
            problem_id=None,
            announcement="We would like to announce something.",
        )


def test_can_create_announcement_permission_matrix(
    running_contest: Contest,
    admin_user: User,
    judge_user: User,
    another_judge_user: User,
    team_user: User,
    uberadmin: UberAdmin,
) -> None:
    running_contest.chief_judge_id = judge_user.id

    assert can_create_announcement(admin_user, running_contest)
    assert can_create_announcement(judge_user, running_contest)
    assert can_create_announcement(another_judge_user, running_contest)
    assert not can_create_announcement(team_user, running_contest)
    assert not can_create_announcement(uberadmin, running_contest)

    _make_upcoming(running_contest)

    assert can_create_announcement(admin_user, running_contest)
    assert can_create_announcement(judge_user, running_contest)
    assert not can_create_announcement(another_judge_user, running_contest)
    assert not can_create_announcement(uberadmin, running_contest)

    assert is_chief_judge(judge_user, running_contest)
    assert not is_chief_judge(another_judge_user, running_contest)
    assert not is_chief_judge(uberadmin, running_contest)

    # Only a JUDGE-role user can be chief judge: an admin whose id matches
    # chief_judge_id is not the chief judge, but still passes the announcement
    # gate through the ADMIN branch of has_chief_authority.
    running_contest.chief_judge_id = admin_user.id
    assert not is_chief_judge(admin_user, running_contest)
    assert can_create_announcement(admin_user, running_contest)


def test_teams_can_only_request_clarifications_while_contest_is_running(
    running_contest: Contest,
    team_user: User,
    judge_user: User,
    admin_user: User,
) -> None:
    assert can_request_clarification(team_user, running_contest)
    assert not can_request_clarification(judge_user, running_contest)
    assert not can_request_clarification(admin_user, running_contest)

    _make_upcoming(running_contest)
    assert not can_request_clarification(team_user, running_contest)

    _make_past(running_contest)
    assert not can_request_clarification(team_user, running_contest)


async def test_team_can_view_public_clarification_before_contest_starts(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    admin_user: User,
) -> None:
    """List visibility is independent from the team's request window."""
    _make_upcoming(running_contest)
    announcement = await create_announcement(
        session,
        running_contest,
        admin_user,
        problem_id=None,
        announcement="Read this before the contest starts.",
    )

    team_view = await list_clarifications(session, running_contest, team_user)

    assert [clarification.id for clarification in team_view] == [announcement.id]
