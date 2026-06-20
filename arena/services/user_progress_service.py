#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Queries for Arena user progress shown on the profile page."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_users import ArenaUser
from arena.services.pagination_service import Pagination, PaginationParams, clamp_page
from shared.db_schema.arena import (
    arena_problem_categories,
    arena_problem_category_map,
    arena_problem_ratings,
    arena_problem_solvers,
    arena_problem_tried,
    arena_problems,
)

ProgressRowSelect = Select[tuple[str, int, str, float, datetime]]


@dataclass(frozen=True)
class ProgressCategory:
    """Category chip data displayed with a progress problem row."""

    name: str


@dataclass(frozen=True)
class ProgressProblemRow:
    """A problem row displayed in an Arena user's progress lists."""

    problem_id: str
    arena_number: int
    title: str
    categories: list[ProgressCategory]
    rating: float
    activity_at: datetime


@dataclass(frozen=True)
class UserProgressSummary:
    """Top-line progress summary for an Arena user."""

    solved_total: int
    user_rating: int


@dataclass(frozen=True)
class UserProgress:
    """Full profile progress data with two independent paginated lists."""

    summary: UserProgressSummary
    solved: Pagination[ProgressProblemRow]
    attempted: Pagination[ProgressProblemRow]


def _count_query(statement: ProgressRowSelect) -> Select[tuple[int]]:
    """Build a count query from a row-select statement.

    Args:
        statement: Base select without limit or offset.

    Returns:
        Select[tuple[int]]: Count over the base query.
    """
    return select(func.count()).select_from(statement.order_by(None).subquery())


async def _paginate_progress_rows(
    session: AsyncSession,
    statement: ProgressRowSelect,
    params: PaginationParams,
) -> Pagination[ProgressProblemRow]:
    """Run a progress row query with count, page clamping, and row conversion.

    Args:
        session: Active database session.
        statement: Base row select.
        params: Requested page settings.

    Returns:
        Pagination[ProgressProblemRow]: Paginated progress rows.
    """
    total = await session.scalar(_count_query(statement)) or 0
    page = clamp_page(params.page, total=total, per_page=params.per_page)
    rows = (
        await session.execute(
            statement.limit(params.per_page).offset((page - 1) * params.per_page),
        )
    ).all()
    categories_by_problem_id = await _load_categories_for_problems(
        session,
        [problem_id for problem_id, *_ in rows],
    )
    items = [
        ProgressProblemRow(
            problem_id=problem_id,
            arena_number=arena_number,
            title=title,
            categories=categories_by_problem_id.get(problem_id, []),
            rating=rating,
            activity_at=activity_at,
        )
        for problem_id, arena_number, title, rating, activity_at in rows
    ]
    return Pagination(items=items, page=page, per_page=params.per_page, total=total)


async def _load_categories_for_problems(
    session: AsyncSession,
    problem_ids: list[str],
) -> dict[str, list[ProgressCategory]]:
    """Return category chip data keyed by problem id.

    Args:
        session: Active database session.
        problem_ids: Problem ids from the current progress page.

    Returns:
        dict[str, list[ProgressCategory]]: Categories ordered by name for each problem.
    """
    if not problem_ids:
        return {}

    rows = (
        await session.execute(
            select(
                arena_problem_category_map.c.problem_id,
                arena_problem_categories.c.name,
            )
            .select_from(
                arena_problem_category_map.join(
                    arena_problem_categories,
                    arena_problem_category_map.c.category_id == arena_problem_categories.c.id,
                )
            )
            .where(arena_problem_category_map.c.problem_id.in_(problem_ids))
            .order_by(arena_problem_categories.c.name.asc())
        )
    ).all()

    categories_by_problem_id: dict[str, list[ProgressCategory]] = {}
    for problem_id, category_name in rows:
        categories_by_problem_id.setdefault(problem_id, []).append(
            ProgressCategory(name=category_name),
        )
    return categories_by_problem_id


def _solved_statement(user_id: str) -> ProgressRowSelect:
    """Build the solved-problems progress query.

    Rating values are returned as display-scale floats (internal / 10.0).

    Args:
        user_id: Arena user id.

    Returns:
        ProgressRowSelect: Query for solved problem rows.
    """
    return (
        select(
            arena_problems.c.id,
            arena_problems.c.arena_number,
            arena_problems.c.title,
            func.coalesce(arena_problem_ratings.c.rating, 0) / 10.0,
            arena_problem_solvers.c.solved_at,
        )
        .select_from(
            arena_problem_solvers.join(
                arena_problems,
                arena_problem_solvers.c.problem_id == arena_problems.c.id,
            ).outerjoin(
                arena_problem_ratings,
                arena_problem_ratings.c.problem_id == arena_problems.c.id,
            )
        )
        .where(arena_problem_solvers.c.user_id == user_id)
        .order_by(arena_problem_solvers.c.solved_at.desc(), arena_problems.c.title.asc())
    )


def _attempted_statement(user_id: str) -> ProgressRowSelect:
    """Build the attempted-but-unsolved progress query.

    Rating values are returned as display-scale floats (internal / 10.0).

    Args:
        user_id: Arena user id.

    Returns:
        ProgressRowSelect: Query for attempted rows.
    """
    return (
        select(
            arena_problems.c.id,
            arena_problems.c.arena_number,
            arena_problems.c.title,
            func.coalesce(arena_problem_ratings.c.rating, 0) / 10.0,
            arena_problem_tried.c.last_tried_at,
        )
        .select_from(
            arena_problem_tried.join(
                arena_problems,
                arena_problem_tried.c.problem_id == arena_problems.c.id,
            )
            .outerjoin(
                arena_problem_ratings,
                arena_problem_ratings.c.problem_id == arena_problems.c.id,
            )
            .outerjoin(
                arena_problem_solvers,
                (arena_problem_solvers.c.problem_id == arena_problem_tried.c.problem_id)
                & (arena_problem_solvers.c.user_id == arena_problem_tried.c.user_id),
            )
        )
        .where(
            arena_problem_tried.c.user_id == user_id,
            arena_problem_solvers.c.problem_id.is_(None),
        )
        .order_by(arena_problem_tried.c.last_tried_at.desc(), arena_problems.c.title.asc())
    )


def make_progress_summary(user: ArenaUser) -> UserProgressSummary:
    """Build a summary from user attributes without any database queries.

    Args:
        user: Arena user whose cached solved count and rating are read.

    Returns:
        UserProgressSummary: Summary built from in-memory user attributes.
    """
    return UserProgressSummary(
        solved_total=user.solved_problems or 0,
        user_rating=user.user_rating or 0,
    )


async def get_solved_progress(
    *,
    session: AsyncSession,
    user: ArenaUser,
    params: PaginationParams,
) -> Pagination[ProgressProblemRow]:
    """Return the paginated solved-problems list for one Arena user.

    Args:
        session: Active database session.
        user: Arena user whose solved problems are listed.
        params: Pagination settings for the solved list.

    Returns:
        Pagination[ProgressProblemRow]: Paginated solved problem rows.
    """
    return await _paginate_progress_rows(session, _solved_statement(user.id), params)


async def get_attempted_progress(
    *,
    session: AsyncSession,
    user: ArenaUser,
    params: PaginationParams,
) -> Pagination[ProgressProblemRow]:
    """Return the paginated attempted-but-unsolved list for one Arena user.

    Args:
        session: Active database session.
        user: Arena user whose attempted problems are listed.
        params: Pagination settings for the attempted list.

    Returns:
        Pagination[ProgressProblemRow]: Paginated attempted problem rows.
    """
    return await _paginate_progress_rows(session, _attempted_statement(user.id), params)


async def get_user_progress(
    *,
    session: AsyncSession,
    user: ArenaUser,
    solved_params: PaginationParams,
    attempted_params: PaginationParams,
) -> UserProgress:
    """Fetch profile progress data for one Arena user.

    Args:
        session: Active database session.
        user: Arena user whose progress is being displayed.
        solved_params: Pagination settings for solved problems.
        attempted_params: Pagination settings for attempted problems.

    Returns:
        UserProgress: Summary plus both paginated lists.
    """
    solved = await _paginate_progress_rows(session, _solved_statement(user.id), solved_params)
    attempted = await _paginate_progress_rows(session, _attempted_statement(user.id), attempted_params)
    return UserProgress(
        summary=make_progress_summary(user),
        solved=solved,
        attempted=attempted,
    )
