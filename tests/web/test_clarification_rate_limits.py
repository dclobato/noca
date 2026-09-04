#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Per-team write throttles on clarification requests.

The advisory locks are no-ops on the SQLite test database, so these tests
exercise the counting rules, not the serialization they protect.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.clarification import clarifications
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.users import User
from web.services.clarification_service import (
    ClarificationRateLimitError,
    TooManyUnansweredClarificationsError,
    create_announcement,
    create_clarification,
)

_WINDOW = 600


async def _ask(session: AsyncSession, contest: Contest, team: User, **limits: int) -> None:
    """Create one clarification with the given limits.

    Args:
        session: The async database session.
        contest: The running contest.
        team: The asking team.
        **limits: Overrides for the throttle keyword arguments.
    """
    await create_clarification(session, contest, team, problem_id=None, question="Is N <= 100?", **limits)  # type: ignore[arg-type]


async def _count_questions(session: AsyncSession, team_id: str) -> int:
    """Count a team's own (non-announcement) clarifications.

    Args:
        session: The async database session.
        team_id: The team whose rows are counted.

    Returns:
        The number of matching rows.
    """
    rows = (
        await session.execute(
            select(clarifications.c.id).where(
                clarifications.c.team_id == team_id,
                clarifications.c.is_announcement.is_(False),
            )
        )
    ).all()
    return len(rows)


async def _update_oldest(session: AsyncSession, team_id: str, **values: object) -> None:
    """Apply *values* to the team's oldest unanswered clarification.

    Args:
        session: The async database session.
        team_id: The team whose row is updated.
        **values: Column values to set.
    """
    row_id = await session.scalar(
        select(clarifications.c.id)
        .where(clarifications.c.team_id == team_id, clarifications.c.answered_at.is_(None))
        .order_by(clarifications.c.created_at)
        .limit(1)
    )
    await session.execute(update(clarifications).where(clarifications.c.id == row_id).values(**values))


async def test_unanswered_limit_refuses_the_fourth_question(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    for _ in range(3):
        await _ask(session, running_contest, team_user, max_open_clarifications=3, rate_limit_max_requests=0)

    with pytest.raises(TooManyUnansweredClarificationsError) as excinfo:
        await _ask(session, running_contest, team_user, max_open_clarifications=3, rate_limit_max_requests=0)

    assert excinfo.value.open_count == 3
    assert excinfo.value.limit == 3
    assert await _count_questions(session, team_user.id) == 3


async def test_unanswered_limit_is_released_when_a_judge_answers(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    for _ in range(3):
        await _ask(session, running_contest, team_user, max_open_clarifications=3, rate_limit_max_requests=0)
    await _update_oldest(session, team_user.id, answered_at=datetime.now(UTC), answer="Yes.")

    await _ask(session, running_contest, team_user, max_open_clarifications=3, rate_limit_max_requests=0)

    assert await _count_questions(session, team_user.id) == 4


async def test_hidden_clarifications_do_not_hold_the_unanswered_slot(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    for _ in range(3):
        await _ask(session, running_contest, team_user, max_open_clarifications=3, rate_limit_max_requests=0)
    await _update_oldest(session, team_user.id, hidden=True, hidden_at=datetime.now(UTC))

    await _ask(session, running_contest, team_user, max_open_clarifications=3, rate_limit_max_requests=0)

    assert await _count_questions(session, team_user.id) == 4


async def test_window_refuses_the_sixth_question_and_names_a_retry_time(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    for _ in range(5):
        await _ask(session, running_contest, team_user, max_open_clarifications=0, rate_limit_max_requests=5)

    with pytest.raises(ClarificationRateLimitError) as excinfo:
        await _ask(session, running_contest, team_user, max_open_clarifications=0, rate_limit_max_requests=5)

    assert excinfo.value.next_allowed_at > datetime.now(UTC)
    assert await _count_questions(session, team_user.id) == 5


async def test_window_rolls_over(session: AsyncSession, running_contest: Contest, team_user: User) -> None:
    for _ in range(5):
        await _ask(session, running_contest, team_user, max_open_clarifications=0, rate_limit_max_requests=5)
    await session.execute(
        update(clarifications)
        .where(clarifications.c.team_id == team_user.id)
        .values(created_at=datetime.now(UTC) - timedelta(seconds=_WINDOW + 1))
    )

    await _ask(session, running_contest, team_user, max_open_clarifications=0, rate_limit_max_requests=5)

    assert await _count_questions(session, team_user.id) == 6


async def test_limits_are_per_team(
    session: AsyncSession, running_contest: Contest, team_user: User, another_team_user: User
) -> None:
    for _ in range(3):
        await _ask(session, running_contest, team_user, max_open_clarifications=3, rate_limit_max_requests=5)

    await _ask(session, running_contest, another_team_user, max_open_clarifications=3, rate_limit_max_requests=5)

    assert await _count_questions(session, another_team_user.id) == 1


async def test_zero_disables_both_rules(session: AsyncSession, running_contest: Contest, team_user: User) -> None:
    for _ in range(10):
        await _ask(session, running_contest, team_user, max_open_clarifications=0, rate_limit_max_requests=0)

    assert await _count_questions(session, team_user.id) == 10


async def test_announcements_are_not_throttled_and_do_not_count_against_a_team(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    team_user: User,
    contest_problem: Problem,
) -> None:
    for index in range(10):
        await create_announcement(
            session,
            running_contest,
            judge_user,
            problem_id=contest_problem.id,
            announcement=f"Announcement {index}",
        )

    # The judge's own row space is untouched, and a team may still ask.
    await _ask(session, running_contest, team_user, max_open_clarifications=1, rate_limit_max_requests=1)

    assert await _count_questions(session, judge_user.id) == 0
    assert await _count_questions(session, team_user.id) == 1
