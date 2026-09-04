#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Teacher-facing query helpers for Arena problem-set management pages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from arena.models.arena_classes import ArenaClass
from arena.models.arena_problem_sets import ArenaProblemSet
from arena.services import arena_problem_set_report_service
from arena.services.arena_class_service import _active_members_subquery
from arena.services.arena_problem_set_service import (
    ArenaProblemSetNotFoundError,
    _as_utc,
    _assert_teacher,
    _is_accepting,
    _load_set_and_class,
    _set_tied_verdicts,
)
from arena.services.pagination_service import Pagination, PaginationParams, clamp_page
from arena.services.problem_search_service import prepare_problem_picker_search
from shared.db_schema.arena import (
    arena_problem_categories,
    arena_problem_category_map,
    arena_problem_ratings,
    arena_problem_set_problems,
    arena_problem_set_user_snapshots,
    arena_problems,
    arena_users,
)
from shared.enumerations import ArenaRole
from shared.services.arena_difficulty_display import DifficultyDisplay, difficulty_display

ProblemSetManagementSort = Literal["deadline", "name", "starts_on"]
SortDir = Literal["asc", "desc"]


@dataclass(frozen=True)
class ProblemSetManagementRow:
    """One row in the teacher problem-set list."""

    set_id: str
    class_id: str
    name: str
    description: str | None
    starts_on: datetime | None
    deadline: datetime | None
    problem_count: int
    is_accepting: bool


@dataclass(frozen=True)
class ProblemSetProblemManagementRow:
    """One problem row in the manage-problems page."""

    problem_id: str
    arena_number: int
    title: str
    categories: tuple[str, ...]
    difficulty: DifficultyDisplay


@dataclass
class _ProblemAccumulator:
    """Mutable accumulator while collapsing problem rows with their categories."""

    problem_id: str
    arena_number: int
    title: str
    categories: list[str]
    difficulty: DifficultyDisplay


@dataclass(frozen=True)
class ProblemAutocompleteRow:
    """Autocomplete suggestion when adding a problem to a set."""

    problem_id: str
    arena_number: int
    title: str
    difficulty: DifficultyDisplay


@dataclass(frozen=True)
class ReportProblemColumn:
    """One problem column in the teacher report matrix."""

    problem_id: str
    arena_number: int


@dataclass(frozen=True)
class ReportStudentRow:
    """One student row in the teacher report matrix."""

    user_id: str
    user_name: str
    verdicts: tuple[str | None, ...]
    snapshot_rating: int | None
    avatar_revision: int


@dataclass(frozen=True)
class TeacherProblemSetReport:
    """UI-ready report data for the teacher problem-set page."""

    set_id: str
    name: str
    description: str | None
    starts_on: datetime | None
    deadline: datetime | None
    problems: tuple[ReportProblemColumn, ...]
    students: tuple[ReportStudentRow, ...]
    snapshot_available: bool
    no_ac_student_count: int
    no_submission_student_count: int


def normalize_problem_set_sort(
    value: str | None,
    default: ProblemSetManagementSort = "deadline",
) -> ProblemSetManagementSort:
    """Normalize a teacher problem-set sort query parameter."""
    if value == "name":
        return "name"
    if value == "starts_on":
        return "starts_on"
    if value == "deadline":
        return "deadline"
    return default


def normalize_sort_dir(value: str | None, default: SortDir = "desc") -> SortDir:
    """Normalize a sort direction query parameter."""
    if value == "asc":
        return "asc"
    if value == "desc":
        return "desc"
    return default


def _order_for_sort(sort: ProblemSetManagementSort, direction: SortDir) -> Any:
    """Return the ORDER BY expression for teacher problem-set lists."""
    if sort == "name":
        col: InstrumentedAttribute[str] | InstrumentedAttribute[datetime | None] = ArenaProblemSet.name
    elif sort == "starts_on":
        col = ArenaProblemSet.starts_on
    else:
        col = ArenaProblemSet.deadline
    return col.desc() if direction == "desc" else col.asc()


async def list_problem_sets_paginated(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    class_id: str,
    now: datetime,
    params: PaginationParams,
    sort: ProblemSetManagementSort = "deadline",
    direction: SortDir = "desc",
) -> Pagination[ProblemSetManagementRow]:
    """Return the paginated teacher-facing problem-set list for one class."""
    arena_class = await session.get(ArenaClass, class_id)
    if arena_class is None:
        raise ArenaProblemSetNotFoundError("Class does not exist.")
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)

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
    set_ids = [problem_set.id for problem_set in problem_sets]

    problem_counts: dict[str, int] = {}
    if set_ids:
        problem_count_rows = await session.execute(
            select(
                arena_problem_set_problems.c.problem_set_id,
                func.count(arena_problem_set_problems.c.problem_id),
            )
            .where(arena_problem_set_problems.c.problem_set_id.in_(set_ids))
            .group_by(arena_problem_set_problems.c.problem_set_id)
        )
        problem_counts = {set_id: int(count) for set_id, count in problem_count_rows.all()}

    items = [
        ProblemSetManagementRow(
            set_id=problem_set.id,
            class_id=problem_set.class_id,
            name=problem_set.name,
            description=problem_set.description,
            starts_on=problem_set.starts_on,
            deadline=problem_set.deadline,
            problem_count=problem_counts.get(problem_set.id, 0),
            is_accepting=_is_accepting(problem_set, now),
        )
        for problem_set in problem_sets
    ]
    return Pagination(items=items, page=page, per_page=params.per_page, total=total)


async def list_problem_set_problems(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    set_id: str,
) -> list[ProblemSetProblemManagementRow]:
    """Return teacher-facing problem rows for one problem set."""
    _problem_set, arena_class = await _load_set_and_class(session, set_id)
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)
    rows = await session.execute(
        select(
            arena_problems.c.id,
            arena_problems.c.arena_number,
            arena_problems.c.title,
            arena_problem_categories.c.name.label("category_name"),
            arena_problem_ratings.c.rating,
            arena_problem_ratings.c.attempted_users,
            arena_problems.c.expected_difficulty,
        )
        .select_from(
            arena_problem_set_problems.join(
                arena_problems,
                arena_problem_set_problems.c.problem_id == arena_problems.c.id,
            )
            .outerjoin(
                arena_problem_category_map,
                arena_problem_category_map.c.problem_id == arena_problems.c.id,
            )
            .outerjoin(
                arena_problem_categories,
                arena_problem_categories.c.id == arena_problem_category_map.c.category_id,
            )
            .outerjoin(arena_problem_ratings, arena_problem_ratings.c.problem_id == arena_problems.c.id)
        )
        .where(arena_problem_set_problems.c.problem_set_id == set_id)
        .order_by(arena_problems.c.arena_number.asc(), arena_problem_categories.c.name.asc())
    )
    items: dict[str, _ProblemAccumulator] = {}
    for row in rows:
        item = items.setdefault(
            row.id,
            _ProblemAccumulator(
                problem_id=row.id,
                arena_number=row.arena_number,
                title=row.title,
                categories=[],
                difficulty=difficulty_display(row.rating, row.attempted_users, row.expected_difficulty),
            ),
        )
        if row.category_name and row.category_name not in item.categories:
            item.categories.append(row.category_name)
    return [
        ProblemSetProblemManagementRow(
            problem_id=item.problem_id,
            arena_number=item.arena_number,
            title=item.title,
            categories=tuple(item.categories),
            difficulty=item.difficulty,
        )
        for item in items.values()
    ]


async def search_set_candidate_problems(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    set_id: str,
    query: str,
    limit: int = 10,
) -> list[ProblemAutocompleteRow]:
    """Return autocomplete candidates for adding new problems to a set."""
    _problem_set, arena_class = await _load_set_and_class(session, set_id)
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)
    clean_query = query.strip()
    existing = select(arena_problem_set_problems.c.problem_id).where(
        arena_problem_set_problems.c.problem_set_id == set_id
    )
    stmt = (
        select(
            arena_problems.c.id,
            arena_problems.c.arena_number,
            arena_problems.c.title,
            arena_problem_ratings.c.rating,
            arena_problem_ratings.c.attempted_users,
            arena_problems.c.expected_difficulty,
        )
        .select_from(
            arena_problems.outerjoin(arena_problem_ratings, arena_problem_ratings.c.problem_id == arena_problems.c.id)
        )
        .where(arena_problems.c.enabled.is_(True), arena_problems.c.id.not_in(existing))
    )
    if clean_query:
        expressions = await prepare_problem_picker_search(session, clean_query)
        stmt = stmt.where(expressions.predicate)
        # Both PostgreSQL and SQLite reject a bare constant such as
        # ``ORDER BY false``. The exact-number expression is constant for every
        # nonnumeric query, so wrap it in CASE before applying descending order.
        exact_number_order = case((expressions.exact_number_match, 1), else_=0)
        if session.get_bind().dialect.name == "postgresql":
            stmt = stmt.order_by(
                exact_number_order.desc(),
                expressions.full_text_match.desc(),
                expressions.full_text_rank.desc(),
                expressions.trigram_rank.desc(),
                arena_problems.c.arena_number.asc(),
            )
        else:
            stmt = stmt.order_by(exact_number_order.desc(), arena_problems.c.arena_number.asc())
    else:
        stmt = stmt.order_by(arena_problems.c.arena_number.asc())
    stmt = stmt.limit(max(1, min(limit, 20)))
    rows = await session.execute(stmt)
    return [
        ProblemAutocompleteRow(
            problem_id=row.id,
            arena_number=row.arena_number,
            title=row.title,
            difficulty=difficulty_display(row.rating, row.attempted_users, row.expected_difficulty),
        )
        for row in rows
    ]


async def build_teacher_problem_set_report(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    set_id: str,
    now: datetime,
) -> TeacherProblemSetReport:
    """Return UI-ready report data for the teacher problem-set report page."""
    problem_set, arena_class = await _load_set_and_class(session, set_id)
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)
    active = _active_members_subquery().subquery()
    user_rows = await session.execute(
        select(arena_users.c.id, arena_users.c.nome, arena_users.c.avatar_revision)
        .select_from(active.join(arena_users, arena_users.c.id == active.c.user_id))
        .where(
            active.c.class_id == arena_class.id,
            arena_users.c.ativo.is_(True),
        )
        .order_by(arena_users.c.nome.asc())
    )
    problems = tuple(
        ReportProblemColumn(problem_id=row.problem_id, arena_number=row.arena_number)
        for row in await list_problem_set_problems(
            session,
            actor_id=actor_id,
            actor_role=actor_role,
            set_id=set_id,
        )
    )
    grouped = await _set_tied_verdicts(session, set_id)
    snapshot_available = False
    snapshot_totals: dict[str, int] = {}
    if problem_set.deadline is not None and _as_utc(problem_set.deadline) <= _as_utc(now):
        snapshot_available = (
            await session.scalar(
                select(arena_problem_set_user_snapshots.c.problem_set_id)
                .where(arena_problem_set_user_snapshots.c.problem_set_id == set_id)
                .limit(1)
            )
            is not None
        )
        if snapshot_available:
            totals = await session.execute(
                select(
                    arena_problem_set_user_snapshots.c.user_id,
                    arena_problem_set_user_snapshots.c.total_rating,
                ).where(arena_problem_set_user_snapshots.c.problem_set_id == set_id)
            )
            snapshot_totals = {row.user_id: row.total_rating for row in totals}

    students = []
    for user_id, user_name, avatar_revision in user_rows.all():
        verdicts = tuple(
            arena_problem_set_report_service.best_verdict(grouped.get((user_id, problem.problem_id), []))
            for problem in problems
        )
        students.append(
            ReportStudentRow(
                user_id=user_id,
                user_name=user_name,
                verdicts=verdicts,
                snapshot_rating=snapshot_totals.get(user_id) if snapshot_available else None,
                avatar_revision=avatar_revision,
            )
        )
    student_tuple = tuple(students)
    return TeacherProblemSetReport(
        set_id=problem_set.id,
        name=problem_set.name,
        description=problem_set.description,
        starts_on=problem_set.starts_on,
        deadline=problem_set.deadline,
        problems=problems,
        students=student_tuple,
        snapshot_available=snapshot_available,
        no_ac_student_count=sum(1 for s in student_tuple if "AC" not in s.verdicts),
        no_submission_student_count=sum(1 for s in student_tuple if all(v is None for v in s.verdicts)),
    )
