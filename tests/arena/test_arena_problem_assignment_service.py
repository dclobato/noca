#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for problem-detail problem-set assignment services."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_classes  # noqa: F401
import arena.models.arena_problem_sets  # noqa: F401
import arena.models.arena_problems  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.models.arena_classes import ArenaClass
from arena.models.arena_problem_sets import ArenaProblemSet
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_users import ArenaUser
from arena.services import arena_problem_assignment_service as service
from shared.db_schema.arena import arena_problem_set_problems
from shared.enumerations import ArenaRole

NOW = datetime(2026, 7, 25, 15, 0, tzinfo=UTC)
TODAY = NOW.date()


async def _user(session: AsyncSession, role: ArenaRole) -> ArenaUser:
    """Create an active Arena user."""
    user = ArenaUser(
        nome=f"User {uuid.uuid4().hex[:6]}",
        email_normalizado=f"{uuid.uuid4().hex}@test.example",
        dta_nascimento=date(1990, 1, 1),
        role=role,
    )
    user.password = "Senha@Forte1!"
    user.ativo = True
    session.add(user)
    await session.flush()
    return user


async def _class(
    session: AsyncSession,
    teacher: ArenaUser,
    *,
    name: str,
    starts_on: date,
    finishes_on: date,
) -> ArenaClass:
    """Create an Arena class."""
    arena_class = ArenaClass(
        name=name,
        teacher_id=teacher.id,
        starts_on=starts_on,
        finishes_on=finishes_on,
    )
    session.add(arena_class)
    await session.flush()
    return arena_class


async def _set(
    session: AsyncSession,
    arena_class: ArenaClass,
    *,
    name: str,
    deadline: datetime | None,
    starts_on: datetime | None = None,
) -> ArenaProblemSet:
    """Create a problem set."""
    problem_set = ArenaProblemSet(
        class_id=arena_class.id,
        name=name,
        starts_on=starts_on,
        deadline=deadline,
    )
    session.add(problem_set)
    await session.flush()
    return problem_set


async def _problem(session: AsyncSession, owner: ArenaUser) -> ArenaProblem:
    """Create an enabled problem."""
    problem = ArenaProblem(
        arena_number=int(uuid.uuid4().int % 1_000_000_000) + 1,
        title="Assignment problem",
        owner_id=owner.id,
        problem_statement="Statement",
        enabled=True,
    )
    session.add(problem)
    await session.flush()
    return problem


async def _assign(
    session: AsyncSession,
    problem_set: ArenaProblemSet,
    problem: ArenaProblem,
) -> None:
    """Insert one problem-set membership."""
    await session.execute(
        arena_problem_set_problems.insert().values(
            problem_set_id=problem_set.id,
            problem_id=problem.id,
        )
    )


@pytest.mark.asyncio
async def test_overview_requires_exact_judge_with_owned_ongoing_class(
    session: AsyncSession,
) -> None:
    """Admins, users, and judges without ongoing owned classes see no overview."""
    judge = await _user(session, ArenaRole.ARENA_JUDGE)
    admin = await _user(session, ArenaRole.ARENA_ADMIN)
    user = await _user(session, ArenaRole.ARENA_USER)
    problem = await _problem(session, judge)
    ended = await _class(
        session,
        judge,
        name="Ended",
        starts_on=TODAY - timedelta(days=20),
        finishes_on=TODAY - timedelta(days=1),
    )
    await _set(session, ended, name="Ended set", deadline=None)

    for actor in (admin, user, judge):
        assert (
            await service.get_problem_assignment_overview(
                session,
                actor_id=actor.id,
                actor_role=actor.role,
                problem_id=problem.id,
                today=TODAY,
                now=NOW,
            )
            is None
        )

    ongoing = await _class(
        session,
        judge,
        name="Ongoing",
        starts_on=TODAY,
        finishes_on=TODAY,
    )
    empty_overview = await service.get_problem_assignment_overview(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        problem_id=problem.id,
        today=TODAY,
        now=NOW,
    )
    assert empty_overview is not None
    assert empty_overview.existing == ()
    assert empty_overview.eligible == ()

    await _set(session, ongoing, name="Open", deadline=None)
    overview = await service.get_problem_assignment_overview(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        problem_id=problem.id,
        today=TODAY,
        now=NOW,
    )
    assert overview is not None
    assert [group.class_name for group in overview.eligible] == ["Ongoing"]


@pytest.mark.asyncio
async def test_overview_groups_and_orders_existing_and_eligible_sets(
    session: AsyncSession,
) -> None:
    """The overview applies ownership, scheduling, membership, and ordering rules."""
    teacher = await _user(session, ArenaRole.ARENA_JUDGE)
    other_teacher = await _user(session, ArenaRole.ARENA_JUDGE)
    problem = await _problem(session, teacher)
    recent = await _class(
        session,
        teacher,
        name="Recent",
        starts_on=TODAY + timedelta(days=5),
        finishes_on=TODAY + timedelta(days=30),
    )
    older = await _class(
        session,
        teacher,
        name="Older",
        starts_on=TODAY - timedelta(days=30),
        finishes_on=TODAY + timedelta(days=30),
    )
    foreign = await _class(
        session,
        other_teacher,
        name="Foreign",
        starts_on=TODAY + timedelta(days=10),
        finishes_on=TODAY + timedelta(days=30),
    )

    due_later = await _set(
        session,
        recent,
        name="Due later",
        deadline=NOW + timedelta(days=2),
    )
    due_soon = await _set(
        session,
        recent,
        name="Due soon",
        deadline=NOW + timedelta(days=1),
    )
    open_existing = await _set(session, recent, name="Open existing", deadline=None)
    closed_existing = await _set(
        session,
        recent,
        name="Closed existing",
        deadline=NOW - timedelta(days=1),
    )
    future_target = await _set(
        session,
        recent,
        name="Future start target",
        starts_on=NOW + timedelta(days=10),
        deadline=NOW + timedelta(days=20),
    )
    open_target = await _set(session, older, name="Open target", deadline=None)
    await _set(session, older, name="Closed target", deadline=NOW - timedelta(seconds=1))
    await _set(session, foreign, name="Foreign target", deadline=None)
    for problem_set in (due_later, due_soon, open_existing, closed_existing):
        await _assign(session, problem_set, problem)

    overview = await service.get_problem_assignment_overview(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        problem_id=problem.id,
        today=TODAY,
        now=NOW,
    )

    assert overview is not None
    assert [group.class_name for group in overview.existing] == ["Recent"]
    assert [item.name for item in overview.existing[0].problem_sets] == [
        "Closed existing",
        "Due soon",
        "Due later",
        "Open existing",
    ]
    assert [group.class_name for group in overview.eligible] == ["Recent", "Older"]
    assert [item.set_id for item in overview.eligible[0].problem_sets] == [future_target.id]
    assert [item.set_id for item in overview.eligible[1].problem_sets] == [open_target.id]


@pytest.mark.asyncio
async def test_add_revalidates_target_and_inserts_once(session: AsyncSession) -> None:
    """Mutation rejects stale or unauthorized choices and inserts a valid membership."""
    teacher = await _user(session, ArenaRole.ARENA_JUDGE)
    other = await _user(session, ArenaRole.ARENA_JUDGE)
    admin = await _user(session, ArenaRole.ARENA_ADMIN)
    problem = await _problem(session, teacher)
    ongoing = await _class(
        session,
        teacher,
        name="Owned",
        starts_on=TODAY + timedelta(days=5),
        finishes_on=TODAY + timedelta(days=30),
    )
    ended = await _class(
        session,
        teacher,
        name="Ended",
        starts_on=TODAY - timedelta(days=30),
        finishes_on=TODAY - timedelta(days=1),
    )
    foreign = await _class(
        session,
        other,
        name="Foreign",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=30),
    )
    valid_set = await _set(session, ongoing, name="Valid", deadline=None)
    populated_set = await _set(session, ongoing, name="Populated", deadline=None)
    closed_set = await _set(
        session,
        ongoing,
        name="Closed",
        deadline=NOW - timedelta(seconds=1),
    )
    ended_set = await _set(session, ended, name="Ended", deadline=None)
    foreign_set = await _set(session, foreign, name="Foreign", deadline=None)
    await _assign(session, populated_set, problem)

    common = {
        "session": session,
        "actor_id": teacher.id,
        "actor_role": teacher.role,
        "arena_number": problem.arena_number,
        "today": TODAY,
        "now": NOW,
    }
    with pytest.raises(service.ProblemAssignmentPermissionError):
        await service.add_problem_to_problem_set(**common, problem_set_id=foreign_set.id)
    with pytest.raises(service.ProblemAssignmentSelectionError):
        await service.add_problem_to_problem_set(**common, problem_set_id=ended_set.id)
    with pytest.raises(service.ProblemAssignmentSelectionError):
        await service.add_problem_to_problem_set(**common, problem_set_id=closed_set.id)
    with pytest.raises(service.ProblemAssignmentSelectionError):
        await service.add_problem_to_problem_set(**common, problem_set_id=populated_set.id)
    with pytest.raises(service.ProblemAssignmentSelectionError):
        await service.add_problem_to_problem_set(**common, problem_set_id=str(uuid.uuid4()))
    with pytest.raises(service.ProblemAssignmentProblemNotFoundError):
        await service.add_problem_to_problem_set(
            **{**common, "arena_number": 2_000_000_000},
            problem_set_id=valid_set.id,
        )
    with pytest.raises(service.ProblemAssignmentPermissionError):
        await service.add_problem_to_problem_set(
            **{**common, "actor_id": admin.id, "actor_role": admin.role},
            problem_set_id=valid_set.id,
        )

    await service.add_problem_to_problem_set(**common, problem_set_id=valid_set.id)
    count = await session.scalar(
        select(func.count())
        .select_from(arena_problem_set_problems)
        .where(
            arena_problem_set_problems.c.problem_set_id == valid_set.id,
            arena_problem_set_problems.c.problem_id == problem.id,
        )
    )
    assert count == 1


@pytest.mark.asyncio
async def test_add_rejects_zero_count_from_membership_race(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent duplicate reported by the shared operation is not flashed as success."""
    teacher = await _user(session, ArenaRole.ARENA_JUDGE)
    problem = await _problem(session, teacher)
    arena_class = await _class(
        session,
        teacher,
        name="Race class",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=1),
    )
    problem_set = await _set(session, arena_class, name="Race set", deadline=None)
    add_mock = AsyncMock(return_value=0)
    monkeypatch.setattr(service, "add_problems_to_set", add_mock)

    with pytest.raises(
        service.ProblemAssignmentSelectionError,
        match="already in the selected problem set",
    ):
        await service.add_problem_to_problem_set(
            session,
            actor_id=teacher.id,
            actor_role=teacher.role,
            arena_number=problem.arena_number,
            problem_set_id=problem_set.id,
            today=TODAY,
            now=NOW,
        )

    add_mock.assert_awaited_once()
