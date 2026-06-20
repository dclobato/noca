#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Student-facing query helpers for Arena problem-set pages.

Exposes the same problem-set data as :mod:`arena_problem_set_management_service`
but scoped to the requesting student: submission counts and verdict information
reflect only *that user's* set-tied submissions, not the entire class.

All functions take an explicit ``AsyncSession`` and never commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_classes import ArenaClass
from arena.models.arena_problem_sets import ArenaProblemSet
from arena.services.arena_problem_set_report_service import best_verdict
from arena.services.arena_problem_set_service import (
    ArenaProblemSetNotFoundError,
    _as_utc,
    _assert_can_view,
    _is_accepting,
    _load_set_and_class,
)
from arena.services.pagination_service import Pagination, PaginationParams, clamp_page
from shared.db_schema.arena import (
    arena_problem_set_problems,
    arena_problem_set_user_snapshots,
    arena_problems,
    arena_submission_judgments,
    arena_submissions,
)
from shared.enumerations import ArenaRole, Verdict

StudentProblemSetSort = Literal["deadline", "name"]
SortDir = Literal["asc", "desc"]


@dataclass(frozen=True)
class StudentProblemSetRow:
    """One row in the student problem-set list."""

    set_id: str
    class_id: str
    name: str
    description: str | None
    starts_on: datetime | None
    deadline: datetime | None
    is_accepting: bool
    problem_count: int
    user_ac_count: int
    user_no_submission_count: int


@dataclass(frozen=True)
class StudentProblemRow:
    """One problem row in the student problem-set detail."""

    problem_id: str
    arena_number: int
    title: str
    best_verdict: str | None
    best_verdict_at: datetime | None


@dataclass(frozen=True)
class StudentProblemSetDetail:
    """UI-ready detail data for the student problem-set detail page."""

    set_id: str
    class_id: str
    class_name: str
    name: str
    description: str | None
    starts_on: datetime | None
    deadline: datetime | None
    is_due: bool
    problems: tuple[StudentProblemRow, ...]
    snapshot_available: bool
    user_snapshot_rating: int | None


def _best_verdict_at(entries: list[tuple[str | None, datetime]]) -> datetime | None:
    """Return the submitted_at of the earliest submission that achieved the best verdict."""
    bv = best_verdict(v for v, _ in entries)
    if bv is None:
        return None
    return min((at for v, at in entries if v == bv), default=None)


def normalize_student_ps_sort(
    value: str | None,
    default: StudentProblemSetSort = "deadline",
) -> StudentProblemSetSort:
    """Normalize a student problem-set sort query parameter."""
    if value == "name":
        return "name"
    return default


def normalize_sort_dir(value: str | None, default: SortDir = "desc") -> SortDir:
    """Normalize a sort direction query parameter.

    Args:
        value: Raw query-string value (may be None or any string).
        default: Fallback when value is not ``"asc"`` or ``"desc"``.

    Returns:
        ``"asc"`` or ``"desc"``.
    """
    if value in {"asc", "desc"}:
        return value  # type: ignore[return-value]
    return default


def _order_for_sort(sort: StudentProblemSetSort, direction: SortDir) -> Any:
    """Return the ORDER BY expression for student problem-set lists."""
    col = ArenaProblemSet.name if sort == "name" else ArenaProblemSet.deadline
    return col.desc() if direction == "desc" else col.asc()


async def list_student_problem_sets_paginated(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    class_id: str,
    now: datetime,
    params: PaginationParams,
    sort: StudentProblemSetSort = "deadline",
    direction: SortDir = "desc",
) -> Pagination[StudentProblemSetRow]:
    """Return the paginated student-facing problem-set list for one class.

    Args:
        session: Active async database session.
        actor_id: ID of the requesting user.
        actor_role: Role of the requesting user.
        class_id: UUID of the class whose problem sets to list.
        now: Current UTC datetime used to evaluate accepting/due state.
        params: Pagination parameters (page, per_page).
        sort: Column to sort by (``"deadline"`` or ``"name"``).
        direction: Sort direction (``"asc"`` or ``"desc"``).

    Returns:
        A :class:`Pagination` of :class:`StudentProblemSetRow` items.

    Raises:
        ArenaProblemSetNotFoundError: When the class does not exist.
        ArenaProblemSetPermissionError: When the actor is not an active member.
    """
    arena_class = await session.get(ArenaClass, class_id)
    if arena_class is None:
        raise ArenaProblemSetNotFoundError("Class does not exist.")
    await _assert_can_view(session, arena_class, actor_id=actor_id, actor_role=actor_role)

    total = await session.scalar(
        select(func.count()).select_from(ArenaProblemSet).where(ArenaProblemSet.class_id == class_id)
    )
    total = int(total or 0)
    page = clamp_page(params.page, total=total, per_page=params.per_page)
    stmt = (
        select(ArenaProblemSet)
        .where(ArenaProblemSet.class_id == class_id)
        .order_by(_order_for_sort(sort, direction), ArenaProblemSet.name.asc())
        .limit(params.per_page)
        .offset((page - 1) * params.per_page)
    )
    problem_sets = list((await session.execute(stmt)).scalars())
    set_ids = [ps.id for ps in problem_sets]

    # Map set_id → set of problem_ids for problems in each set
    set_problem_ids: dict[str, set[str]] = {}
    if set_ids:
        problem_rows = await session.execute(
            select(
                arena_problem_set_problems.c.problem_set_id,
                arena_problem_set_problems.c.problem_id,
            ).where(arena_problem_set_problems.c.problem_set_id.in_(set_ids))
        )
        for ps_id, prob_id in problem_rows.all():
            set_problem_ids.setdefault(str(ps_id), set()).add(str(prob_id))

    # Map (set_id, problem_id) → list of verdicts for this user's set-tied submissions
    user_verdicts: dict[tuple[str, str], list[str | None]] = {}
    if set_ids:
        verdict_rows = await session.execute(
            select(
                arena_submissions.c.problem_set_id,
                arena_submissions.c.problem_id,
                arena_submission_judgments.c.final_verdict,
            )
            .select_from(
                arena_submissions.outerjoin(
                    arena_submission_judgments,
                    arena_submission_judgments.c.submission_id == arena_submissions.c.id,
                )
            )
            .where(
                arena_submissions.c.problem_set_id.in_(set_ids),
                arena_submissions.c.user_id == actor_id,
            )
        )
        for r in verdict_rows:
            key = (str(r.problem_set_id), str(r.problem_id))
            user_verdicts.setdefault(key, []).append(r.final_verdict)

    items: list[StudentProblemSetRow] = []
    for ps in problem_sets:
        ps_problems = set_problem_ids.get(ps.id, set())
        ac_count = 0
        no_sub_count = 0
        for prob_id in ps_problems:
            verdicts = user_verdicts.get((ps.id, prob_id), [])
            if Verdict.AC.value in verdicts:
                ac_count += 1
            if not verdicts:
                no_sub_count += 1
        items.append(
            StudentProblemSetRow(
                set_id=ps.id,
                class_id=ps.class_id,
                name=ps.name,
                description=ps.description,
                starts_on=ps.starts_on,
                deadline=ps.deadline,
                is_accepting=_is_accepting(ps, now),
                problem_count=len(ps_problems),
                user_ac_count=ac_count,
                user_no_submission_count=no_sub_count,
            )
        )
    return Pagination(items=items, page=page, per_page=params.per_page, total=total)


async def get_student_problem_set_detail(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    set_id: str,
    now: datetime,
) -> StudentProblemSetDetail:
    """Return UI-ready detail data for the student problem-set detail page.

    Args:
        session: Active async database session.
        actor_id: ID of the requesting user.
        actor_role: Role of the requesting user.
        set_id: UUID of the problem set.
        now: Current UTC datetime used to determine if the set is due.

    Returns:
        A :class:`StudentProblemSetDetail` instance.

    Raises:
        ArenaProblemSetNotFoundError: When the problem set does not exist.
        ArenaProblemSetPermissionError: When the actor is not an active member.
    """
    problem_set, arena_class = await _load_set_and_class(session, set_id)
    await _assert_can_view(session, arena_class, actor_id=actor_id, actor_role=actor_role)

    # Load problems ordered by arena number
    problem_rows = list(
        (
            await session.execute(
                select(arena_problems.c.id, arena_problems.c.arena_number, arena_problems.c.title)
                .select_from(
                    arena_problem_set_problems.join(
                        arena_problems, arena_problem_set_problems.c.problem_id == arena_problems.c.id
                    )
                )
                .where(arena_problem_set_problems.c.problem_set_id == set_id)
                .order_by(arena_problems.c.arena_number)
            )
        ).all()
    )

    # Load this user's set-tied verdicts for this specific set
    verdict_rows = await session.execute(
        select(
            arena_submissions.c.problem_id,
            arena_submissions.c.created_at,
            arena_submission_judgments.c.final_verdict,
        )
        .select_from(
            arena_submissions.outerjoin(
                arena_submission_judgments,
                arena_submission_judgments.c.submission_id == arena_submissions.c.id,
            )
        )
        .where(
            arena_submissions.c.problem_set_id == set_id,
            arena_submissions.c.user_id == actor_id,
        )
    )
    # Map problem_id → list of (verdict, submitted_at)
    user_verdicts: dict[str, list[tuple[str | None, datetime]]] = {}
    for r in verdict_rows:
        user_verdicts.setdefault(str(r.problem_id), []).append((r.final_verdict, r.created_at))

    problems = tuple(
        StudentProblemRow(
            problem_id=str(r.id),
            arena_number=r.arena_number,
            title=r.title,
            best_verdict=best_verdict(v for v, _ in user_verdicts.get(str(r.id), [])),
            best_verdict_at=_best_verdict_at(user_verdicts.get(str(r.id), [])),
        )
        for r in problem_rows
    )

    # Determine if set is past its deadline
    is_due = problem_set.deadline is not None and _as_utc(problem_set.deadline) < _as_utc(now)

    # Check snapshot and fetch this user's total if available
    snapshot_available = False
    user_snapshot_rating: int | None = None
    if is_due:
        has_snapshot = await session.scalar(
            select(arena_problem_set_user_snapshots.c.problem_set_id)
            .where(arena_problem_set_user_snapshots.c.problem_set_id == set_id)
            .limit(1)
        )
        snapshot_available = has_snapshot is not None
        if snapshot_available:
            user_rating = await session.scalar(
                select(arena_problem_set_user_snapshots.c.total_rating).where(
                    arena_problem_set_user_snapshots.c.problem_set_id == set_id,
                    arena_problem_set_user_snapshots.c.user_id == actor_id,
                )
            )
            # No row means the user had no submissions → rating is 0
            user_snapshot_rating = int(user_rating) if user_rating is not None else 0

    return StudentProblemSetDetail(
        set_id=problem_set.id,
        class_id=problem_set.class_id,
        class_name=arena_class.name,
        name=problem_set.name,
        description=problem_set.description,
        starts_on=problem_set.starts_on,
        deadline=problem_set.deadline,
        is_due=is_due,
        problems=problems,
        snapshot_available=snapshot_available,
        user_snapshot_rating=user_snapshot_rating,
    )
