#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Teacher-facing problem-to-problem-set assignment service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_classes import ArenaClass
from arena.models.arena_problem_sets import ArenaProblemSet
from arena.services.arena_problem_set_service import add_problems_to_set
from shared.db_schema.arena import arena_problem_set_problems, arena_problems
from shared.enumerations import ArenaRole


class ProblemAssignmentError(Exception):
    """Base error for problem-detail assignment operations."""


class ProblemAssignmentPermissionError(ProblemAssignmentError):
    """Raised when the actor cannot assign the problem to the selected set."""


class ProblemAssignmentProblemNotFoundError(ProblemAssignmentError):
    """Raised when the public problem number does not identify an enabled problem."""


class ProblemAssignmentSelectionError(ProblemAssignmentError):
    """Raised when a selected problem set is missing or no longer eligible."""


@dataclass(frozen=True)
class ProblemSetAssignment:
    """One problem set shown in an assignment group."""

    set_id: str
    name: str
    deadline: datetime | None


@dataclass(frozen=True)
class ProblemSetAssignmentGroup:
    """Problem sets grouped under their owning class."""

    class_id: str
    class_name: str
    starts_on: date
    finishes_on: date
    problem_sets: tuple[ProblemSetAssignment, ...]


@dataclass(frozen=True)
class ProblemAssignmentOverview:
    """Existing assignments and eligible targets for a teacher."""

    existing: tuple[ProblemSetAssignmentGroup, ...]
    eligible: tuple[ProblemSetAssignmentGroup, ...]


def _as_utc(value: datetime) -> datetime:
    """Return an aware datetime, interpreting naive database values as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _set_sort_key(problem_set: ArenaProblemSet) -> tuple[bool, datetime, str]:
    """Sort dated sets first by earliest deadline and open sets last."""
    deadline = problem_set.deadline
    return (
        deadline is None,
        datetime.max.replace(tzinfo=UTC) if deadline is None else _as_utc(deadline),
        problem_set.name.casefold(),
    )


def _group(
    arena_class: ArenaClass,
    problem_sets: list[ArenaProblemSet],
) -> ProblemSetAssignmentGroup:
    """Build one immutable class/problem-set group."""
    return ProblemSetAssignmentGroup(
        class_id=arena_class.id,
        class_name=arena_class.name,
        starts_on=arena_class.starts_on,
        finishes_on=arena_class.finishes_on,
        problem_sets=tuple(
            ProblemSetAssignment(
                set_id=problem_set.id,
                name=problem_set.name,
                deadline=problem_set.deadline,
            )
            for problem_set in sorted(problem_sets, key=_set_sort_key)
        ),
    )


async def get_problem_assignment_overview(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    problem_id: str,
    today: date,
    now: datetime,
) -> ProblemAssignmentOverview | None:
    """Return assignment groups for an exact judge who owns an ongoing class."""
    if actor_role != ArenaRole.ARENA_JUDGE:
        return None

    classes = list(
        (
            await session.execute(
                select(ArenaClass)
                .where(
                    ArenaClass.teacher_id == actor_id,
                    ArenaClass.finishes_on >= today,
                )
                .order_by(
                    ArenaClass.starts_on.desc(),
                    ArenaClass.name.asc(),
                    ArenaClass.id.asc(),
                )
            )
        ).scalars()
    )
    if not classes:
        return None

    class_ids = [arena_class.id for arena_class in classes]
    problem_sets = list(
        (await session.execute(select(ArenaProblemSet).where(ArenaProblemSet.class_id.in_(class_ids)))).scalars()
    )
    containing_set_ids = set(
        (
            await session.execute(
                select(arena_problem_set_problems.c.problem_set_id).where(
                    arena_problem_set_problems.c.problem_id == problem_id,
                    arena_problem_set_problems.c.problem_set_id.in_([problem_set.id for problem_set in problem_sets]),
                )
            )
        ).scalars()
    )

    sets_by_class: dict[str, list[ArenaProblemSet]] = {class_id: [] for class_id in class_ids}
    for problem_set in problem_sets:
        sets_by_class[problem_set.class_id].append(problem_set)

    existing: list[ProblemSetAssignmentGroup] = []
    eligible: list[ProblemSetAssignmentGroup] = []
    for arena_class in classes:
        class_sets = sets_by_class[arena_class.id]
        existing_sets = [problem_set for problem_set in class_sets if problem_set.id in containing_set_ids]
        eligible_sets = [
            problem_set
            for problem_set in class_sets
            if problem_set.id not in containing_set_ids
            and (problem_set.deadline is None or _as_utc(problem_set.deadline) >= _as_utc(now))
        ]
        if existing_sets:
            existing.append(_group(arena_class, existing_sets))
        if eligible_sets:
            eligible.append(_group(arena_class, eligible_sets))

    return ProblemAssignmentOverview(existing=tuple(existing), eligible=tuple(eligible))


async def add_problem_to_problem_set(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    arena_number: int,
    problem_set_id: str,
    today: date,
    now: datetime,
) -> None:
    """Revalidate and add one problem to an eligible teacher-owned problem set."""
    if actor_role != ArenaRole.ARENA_JUDGE:
        raise ProblemAssignmentPermissionError("Only Arena judges may assign problems.")

    problem_id = await session.scalar(
        select(arena_problems.c.id).where(
            arena_problems.c.arena_number == arena_number,
            arena_problems.c.enabled.is_(True),
        )
    )
    if problem_id is None:
        raise ProblemAssignmentProblemNotFoundError("Problem not found.")

    problem_set = await session.get(ArenaProblemSet, problem_set_id)
    if problem_set is None:
        raise ProblemAssignmentSelectionError("That problem set is no longer available.")
    arena_class = await session.get(ArenaClass, problem_set.class_id)
    if arena_class is None:
        raise ProblemAssignmentSelectionError("That problem set is no longer available.")
    if arena_class.teacher_id != actor_id:
        raise ProblemAssignmentPermissionError("You do not own that problem set.")
    if arena_class.finishes_on < today:
        raise ProblemAssignmentSelectionError("That class has already ended.")
    if problem_set.deadline is not None and _as_utc(problem_set.deadline) < _as_utc(now):
        raise ProblemAssignmentSelectionError("That problem set is already closed.")

    existing = await session.scalar(
        select(arena_problem_set_problems.c.problem_id).where(
            arena_problem_set_problems.c.problem_set_id == problem_set.id,
            arena_problem_set_problems.c.problem_id == problem_id,
        )
    )
    if existing is not None:
        raise ProblemAssignmentSelectionError("This problem is already in the selected problem set.")

    added = await add_problems_to_set(
        session,
        actor_id=actor_id,
        actor_role=actor_role,
        set_id=problem_set.id,
        refs=[problem_id],
    )
    if added == 0:
        raise ProblemAssignmentSelectionError("This problem is already in the selected problem set.")
