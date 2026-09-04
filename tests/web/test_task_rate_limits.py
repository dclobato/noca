#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Per-team write throttles on SOS and print tasks.

The advisory locks are no-ops on the SQLite test database, so these tests
exercise the counting rules, not the serialization they protect -- the same
scope as ``tests/web/test_submission_rate_limit.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.contest import tasks
from shared.enumerations import TaskType
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.users import User
from web.services.task_service import (
    DuplicatePrintTaskError,
    OpenSosTaskLimitError,
    TaskRateLimitError,
    create_balloon_task,
    create_print_task,
    create_sos_task,
)

_WINDOW = 600


async def _backdate_tasks(session: AsyncSession, team_id: str, seconds: int) -> None:
    """Move every one of a team's tasks *seconds* into the past.

    Args:
        session: The async database session.
        team_id: The team whose tasks are moved.
        seconds: How far back to move them.
    """
    await session.execute(
        update(tasks)
        .where(tasks.c.team_id == team_id)
        .values(created_at=datetime.now(UTC) - timedelta(seconds=seconds))
    )


async def _count_tasks(session: AsyncSession, team_id: str, task_type: TaskType) -> int:
    """Count a team's tasks of one type.

    Args:
        session: The async database session.
        team_id: The team whose tasks are counted.
        task_type: The type to count.

    Returns:
        The number of matching rows.
    """
    rows = (
        await session.execute(select(tasks.c.id).where(tasks.c.team_id == team_id, tasks.c.type == task_type))
    ).all()
    return len(rows)


async def _finish_first_open_task(session: AsyncSession, team_id: str) -> None:
    """Mark the team's oldest unfinished task as finished.

    Args:
        session: The async database session.
        team_id: The team whose task is finished.
    """
    task_id = await session.scalar(
        select(tasks.c.id)
        .where(tasks.c.team_id == team_id, tasks.c.finished_at.is_(None))
        .order_by(tasks.c.created_at)
        .limit(1)
    )
    await session.execute(update(tasks).where(tasks.c.id == task_id).values(finished_at=datetime.now(UTC)))


async def _create_sos(session: AsyncSession, contest: Contest, team: User, **limits: int) -> None:
    """Create one SOS task with the given limits.

    Args:
        session: The async database session.
        contest: The running contest.
        team: The requesting team.
        **limits: Overrides for the throttle keyword arguments.
    """
    await create_sos_task(session, contest, team, **limits)  # type: ignore[arg-type]


async def test_open_sos_limit_refuses_the_fourth_open_request(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    for _ in range(3):
        await _create_sos(session, running_contest, team_user, max_open_tasks=3, rate_limit_max_tasks=0)

    with pytest.raises(OpenSosTaskLimitError) as excinfo:
        await _create_sos(session, running_contest, team_user, max_open_tasks=3, rate_limit_max_tasks=0)

    assert excinfo.value.open_count == 3
    assert excinfo.value.limit == 3
    assert await _count_tasks(session, team_user.id, TaskType.SOS) == 3


async def test_open_sos_limit_is_released_when_staff_finishes_a_task(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    for _ in range(3):
        await _create_sos(session, running_contest, team_user, max_open_tasks=3, rate_limit_max_tasks=0)
    await _finish_first_open_task(session, team_user.id)

    await _create_sos(session, running_contest, team_user, max_open_tasks=3, rate_limit_max_tasks=0)

    assert await _count_tasks(session, team_user.id, TaskType.SOS) == 4


async def test_sos_window_refuses_the_sixth_request_and_names_a_retry_time(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    for _ in range(5):
        await _create_sos(session, running_contest, team_user, max_open_tasks=0, rate_limit_max_tasks=5)

    with pytest.raises(TaskRateLimitError) as excinfo:
        await _create_sos(session, running_contest, team_user, max_open_tasks=0, rate_limit_max_tasks=5)

    assert excinfo.value.next_allowed_at > datetime.now(UTC)
    assert await _count_tasks(session, team_user.id, TaskType.SOS) == 5


async def test_sos_window_rolls_over(session: AsyncSession, running_contest: Contest, team_user: User) -> None:
    for _ in range(5):
        await _create_sos(session, running_contest, team_user, max_open_tasks=0, rate_limit_max_tasks=5)
    await _backdate_tasks(session, team_user.id, _WINDOW + 1)

    await _create_sos(session, running_contest, team_user, max_open_tasks=0, rate_limit_max_tasks=5)

    assert await _count_tasks(session, team_user.id, TaskType.SOS) == 6


async def test_sos_limits_are_per_team(
    session: AsyncSession, running_contest: Contest, team_user: User, another_team_user: User
) -> None:
    for _ in range(3):
        await _create_sos(session, running_contest, team_user, max_open_tasks=3, rate_limit_max_tasks=5)

    await _create_sos(session, running_contest, another_team_user, max_open_tasks=3, rate_limit_max_tasks=5)

    assert await _count_tasks(session, another_team_user.id, TaskType.SOS) == 1


async def test_zero_disables_both_sos_rules(session: AsyncSession, running_contest: Contest, team_user: User) -> None:
    for _ in range(10):
        await _create_sos(session, running_contest, team_user, max_open_tasks=0, rate_limit_max_tasks=0)

    assert await _count_tasks(session, team_user.id, TaskType.SOS) == 10


async def test_open_sos_count_ignores_balloon_tasks(
    session: AsyncSession, running_contest: Contest, team_user: User, contest_problem: Problem
) -> None:
    for _ in range(3):
        await create_balloon_task(session, contest=running_contest, problem_id=contest_problem.id, team_id=team_user.id)

    await _create_sos(session, running_contest, team_user, max_open_tasks=3, rate_limit_max_tasks=5)

    assert await _count_tasks(session, team_user.id, TaskType.SOS) == 1


async def test_print_window_refuses_the_eleventh_request(
    session: AsyncSession, running_contest: Contest, team_user: User, contest_problem: Problem
) -> None:
    for index in range(10):
        await create_print_task(
            session,
            running_contest,
            team_user,
            problem_id=contest_problem.id,
            source_code=f"print({index})",
            rate_limit_max_tasks=10,
        )

    with pytest.raises(TaskRateLimitError):
        await create_print_task(
            session,
            running_contest,
            team_user,
            problem_id=contest_problem.id,
            source_code="print(10)",
            rate_limit_max_tasks=10,
        )

    assert await _count_tasks(session, team_user.id, TaskType.PRINT) == 10


async def test_duplicate_print_error_wins_over_the_rate_limit(
    session: AsyncSession, running_contest: Contest, team_user: User, contest_problem: Problem
) -> None:
    await create_print_task(
        session,
        running_contest,
        team_user,
        problem_id=contest_problem.id,
        source_code="print('same')",
        rate_limit_max_tasks=1,
    )

    with pytest.raises(DuplicatePrintTaskError):
        await create_print_task(
            session,
            running_contest,
            team_user,
            problem_id=contest_problem.id,
            source_code="print('same')",
            rate_limit_max_tasks=1,
        )


async def test_print_has_no_open_task_limit(
    session: AsyncSession, running_contest: Contest, team_user: User, contest_problem: Problem
) -> None:
    for index in range(5):
        await create_print_task(
            session,
            running_contest,
            team_user,
            problem_id=contest_problem.id,
            source_code=f"print({index})",
            rate_limit_max_tasks=10,
        )

    assert await _count_tasks(session, team_user.id, TaskType.PRINT) == 5


async def test_sos_and_print_budgets_are_independent(
    session: AsyncSession, running_contest: Contest, team_user: User, contest_problem: Problem
) -> None:
    for _ in range(5):
        await _create_sos(session, running_contest, team_user, max_open_tasks=0, rate_limit_max_tasks=5)

    await create_print_task(
        session,
        running_contest,
        team_user,
        problem_id=contest_problem.id,
        source_code="print('ok')",
        rate_limit_max_tasks=10,
    )

    assert await _count_tasks(session, team_user.id, TaskType.PRINT) == 1
