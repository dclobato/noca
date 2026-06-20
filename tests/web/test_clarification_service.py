"""
Integration/behavioral tests for clarification_service.py.

Each test function maps to one Gherkin scenario from the feature specification.
Tests run against a fresh in-memory SQLite database (see conftest.py).

Note on concurrency: SQLite ignores SELECT FOR UPDATE, but the service uses
an atomic UPDATE ... WHERE judge_id IS NULL pattern which SQLite does enforce.
Sequential calls in the same test correctly verify the concurrency guard.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
import valkey.asyncio as aivalkey
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from shared.services.lock_service import get_lock
from web.models.clarification import Clarification
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.users import UberAdmin, User
from web.services.assorted_utils import minutes_from_contest_start
from web.services.clarification_reaper import AUTO_ANSWER_PLACEHOLDER, conclude_finished_contest_clarifications
from web.services.clarification_service import (
    ClarificationAlreadyAcquiredError,
    ClarificationAlreadyAnsweredError,
    ClarificationHiddenError,
    ClarificationNotAcquiredByActorError,
    ContestNotRunningError,
    ForbiddenClarificationActionError,
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

    problem = Problem(contest_id=stopped_contest.id, title="Old Problem", ordinal=1, color="#aaaaaa")
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
    team_user: User,
) -> None:
    stopped_problem = Problem(
        contest_id=stopped_contest.id,
        title="Stopped Problem",
        ordinal=1,
        color="#00aa00",
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
    clari = Clarification(
        team_id=team_user.id,
        problem_id=stopped_problem.id,
        question="Reap me.",
        created_timestamp_seconds=minutes_from_contest_start(stopped_contest.start_time, stopped_contest.end_time) * 60,
    )
    session.add(clari)
    await session.flush()

    concluded = await conclude_finished_contest_clarifications(session)

    assert concluded == 1
    assert clari.judge_id == owner.id
    assert clari.answer == AUTO_ANSWER_PLACEHOLDER
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
