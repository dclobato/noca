#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Service for Arena user problem favorites (check, toggle, paginate)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from arena.services.pagination_service import Pagination, PaginationParams, clamp_page
from shared.db_schema.arena import (
    arena_problem_categories,
    arena_problem_category_map,
    arena_problem_favorites,
    arena_problem_ratings,
    arena_problems,
)


@dataclass(frozen=True)
class FavoriteProblemRow:
    """A problem row shown in the favorites tab.

    Attributes:
        problem_id: UUID of the problem.
        arena_number: Public sequential problem number.
        title: Problem title.
        categories: Category chip list.
        rating: Display-scale difficulty (0.1–10.0).
        activity_at: Always ``None``; favorites carry no timestamp.
    """

    problem_id: str
    arena_number: int
    title: str
    categories: list[_ProgressCategory]
    rating: float
    activity_at: datetime | None = None


@dataclass(frozen=True)
class _ProgressCategory:
    """Category chip data for a favorites row."""

    name: str


async def is_favorite(
    session: AsyncSession,
    *,
    user_id: str,
    problem_id: str,
) -> bool:
    """Return ``True`` if the problem is in the user's favorites.

    Args:
        session: Active async database session.
        user_id: UUID of the Arena user.
        problem_id: UUID of the ArenaProblem.

    Returns:
        bool: Whether the (user_id, problem_id) pair exists in arena_problem_favorites.
    """
    row = (
        await session.execute(
            select(arena_problem_favorites.c.problem_id).where(
                arena_problem_favorites.c.user_id == user_id,
                arena_problem_favorites.c.problem_id == problem_id,
            )
        )
    ).one_or_none()
    return row is not None


async def get_favorites_for_problems(
    session: AsyncSession,
    *,
    user_id: str,
    problem_ids: list[str],
) -> set[str]:
    """Return the subset of ``problem_ids`` that are favorited by ``user_id``.

    Args:
        session: Active async database session.
        user_id: UUID of the Arena user.
        problem_ids: Problem UUIDs to check in bulk.

    Returns:
        set[str]: UUIDs of favorited problems.
    """
    if not problem_ids:
        return set()
    rows = (
        await session.execute(
            select(arena_problem_favorites.c.problem_id).where(
                arena_problem_favorites.c.user_id == user_id,
                arena_problem_favorites.c.problem_id.in_(problem_ids),
            )
        )
    ).all()
    return {row[0] for row in rows}


async def toggle_favorite(
    session: AsyncSession,
    *,
    user_id: str,
    problem_id: str,
) -> bool:
    """Add or remove a problem from the user's favorites.

    This is idempotent: adding an existing favorite is a no-op; removing a
    non-existing favorite is a no-op.

    Args:
        session: Active async database session.
        user_id: UUID of the Arena user.
        problem_id: UUID of the ArenaProblem.

    Returns:
        bool: ``True`` if the problem is now a favorite, ``False`` otherwise.
    """
    result = await session.execute(
        delete(arena_problem_favorites).where(
            arena_problem_favorites.c.user_id == user_id,
            arena_problem_favorites.c.problem_id == problem_id,
        )
    )
    if result.rowcount > 0:  # type: ignore[attr-defined]
        return False

    await session.execute(
        pg_insert(arena_problem_favorites).values(user_id=user_id, problem_id=problem_id).on_conflict_do_nothing()
    )
    return True


async def _load_categories(
    session: AsyncSession,
    problem_ids: list[str],
) -> dict[str, list[_ProgressCategory]]:
    """Return category chip data keyed by problem id.

    Args:
        session: Active database session.
        problem_ids: Problem ids from the current page.

    Returns:
        dict[str, list[_ProgressCategory]]: Categories per problem.
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
    result: dict[str, list[_ProgressCategory]] = {}
    for problem_id, name in rows:
        result.setdefault(problem_id, []).append(_ProgressCategory(name=name))
    return result


async def get_favorites_paginated(
    session: AsyncSession,
    *,
    user_id: str,
    params: PaginationParams,
) -> Pagination[FavoriteProblemRow]:
    """Return a paginated list of favorite problems for an Arena user.

    Only enabled problems are included. Results are ordered by arena_number ASC.

    Args:
        session: Active async database session.
        user_id: UUID of the Arena user.
        params: Pagination settings.

    Returns:
        Pagination[FavoriteProblemRow]: Paginated favorite problem rows.
    """
    base = (
        select(
            arena_problems.c.id,
            arena_problems.c.arena_number,
            arena_problems.c.title,
            func.coalesce(arena_problem_ratings.c.rating, 0) / 10.0,
        )
        .select_from(
            arena_problem_favorites.join(
                arena_problems,
                arena_problem_favorites.c.problem_id == arena_problems.c.id,
            ).outerjoin(
                arena_problem_ratings,
                arena_problem_ratings.c.problem_id == arena_problems.c.id,
            )
        )
        .where(
            arena_problem_favorites.c.user_id == user_id,
            arena_problems.c.enabled == True,  # noqa: E712
        )
        .order_by(arena_problems.c.arena_number.asc())
    )

    count_stmt = select(func.count()).select_from(base.order_by(None).subquery())
    total = await session.scalar(count_stmt) or 0
    page = clamp_page(params.page, total=total, per_page=params.per_page)

    rows = (await session.execute(base.limit(params.per_page).offset((page - 1) * params.per_page))).all()

    problem_ids = [row[0] for row in rows]
    categories = await _load_categories(session, problem_ids)

    items = [
        FavoriteProblemRow(
            problem_id=problem_id,
            arena_number=arena_number,
            title=title,
            categories=categories.get(problem_id, []),
            rating=float(rating),
        )
        for problem_id, arena_number, title, rating in rows
    ]
    return Pagination(items=items, page=page, per_page=params.per_page, total=total)
