#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public-facing service for browsing enabled Arena problems.

Only ``enabled=True`` problems are exposed by any function here.
No ownership or role filtering applies; this module is for the public
problem list and detail pages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, case, cast, func, or_, select
from sqlalchemy import String as SAString
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from arena.models.arena_problems import ArenaCategory, ArenaProblem, ArenaRatingProblem
from arena.services.pagination_service import Pagination, PaginationParams
from shared.db_schema.arena import arena_affiliations as _affiliations_table
from shared.db_schema.arena import arena_problem_category_map as _cat_map_table
from shared.db_schema.arena import arena_problem_favorites as _favorites_table
from shared.db_schema.arena import arena_problem_solvers as _solvers_table
from shared.db_schema.arena import arena_problem_tried as _tried_table
from shared.db_schema.arena import arena_problems as _problems_table
from shared.db_schema.arena import arena_users as _users_table
from shared.db_schema.arena.arena_rating_history import arena_problem_rating_history
from shared.services.arena_query_helpers import counts_toward_problem_rating


@dataclass(frozen=True)
class AuthorInfo:
    """Display information about a problem's author for the detail page.

    Attributes:
        name: Author's full display name, or ``None`` if the author record is missing.
        affiliation_name: Name of the author's affiliated institution, or ``None``.
        affiliation_country_code: ISO 3166-1 alpha-2 code for the affiliation's country, or ``None``.
    """

    name: str | None
    affiliation_name: str | None
    affiliation_country_code: str | None


_VALID_SORT_KEYS = {
    "title_asc",
    "title_desc",
    "number_asc",
    "number_desc",
    "rating_asc",
    "rating_desc",
    "solvers_asc",
    "solvers_desc",
}

_PUBLIC_PER_PAGE = 25


@dataclass(frozen=True)
class PublicProblemListItem:
    """Single row in the public problem list page.

    Attributes:
        problem: The ``ArenaProblem`` instance.
        rating: Current display-scale problem difficulty (0.1–10.0), or ``None``
            if not yet computed.
        categories: Categories linked to this problem.
        author_name: Display name of the problem author, or ``None`` if missing.
        is_favorite: Whether the viewing user has favorited this problem.
        ac_rate: Fraction of users who solved the problem (0.0–1.0), or ``None``
            if no rating data is available yet.
        is_solved: Whether the viewing user has a first-AC record for this problem.
        solved: Number of distinct non-owner solvers for this problem, or
            ``None`` if no counted solver data is available.
    """

    problem: ArenaProblem
    rating: float | None
    categories: list[ArenaCategory]
    author_name: str | None
    is_favorite: bool = False
    ac_rate: float | None = None
    is_solved: bool = False
    solved: int | None = None


def _apply_sort(stmt: Select[Any], sort_by: str, solver_count: Any) -> Select[Any]:
    """Append ORDER BY clause for the given sort key.

    Args:
        stmt: Base select statement.
        sort_by: One of the ``_VALID_SORT_KEYS`` values.
        solver_count: SQL expression with the live participant solver count.

    Returns:
        Select: The statement with an ORDER BY clause appended.
    """
    if sort_by == "title_desc":
        return stmt.order_by(func.lower(ArenaProblem.title).desc())
    if sort_by == "number_asc":
        return stmt.order_by(ArenaProblem.arena_number.asc())
    if sort_by == "number_desc":
        return stmt.order_by(ArenaProblem.arena_number.desc())
    if sort_by == "rating_asc":
        return stmt.order_by(ArenaRatingProblem.rating.asc().nulls_last())
    if sort_by == "rating_desc":
        return stmt.order_by(ArenaRatingProblem.rating.desc().nulls_first())
    if sort_by == "solvers_asc":
        return stmt.order_by(solver_count.asc(), ArenaProblem.arena_number.asc())
    if sort_by == "solvers_desc":
        return stmt.order_by(solver_count.desc(), ArenaProblem.arena_number.asc())
    # default: title_asc
    return stmt.order_by(func.lower(ArenaProblem.title).asc())


async def list_enabled_problems_paginated(
    session: AsyncSession,
    *,
    page: int,
    per_page: int = _PUBLIC_PER_PAGE,
    search: str = "",
    category_slugs: list[str] | None = None,
    sort_by: str = "number_asc",
    user_id: str | None = None,
) -> Pagination[PublicProblemListItem]:
    """Return a paginated list of enabled problems with search and filter support.

    Search covers arena number, title, source, and the resolved author name.
    Category filter uses AND semantics: a problem must belong to every selected category.

    Args:
        session: Active async database session.
        page: 1-based page number.
        per_page: Number of items per page (default 25).
        search: Free-text search applied to number, title, source, and author name.
        category_slugs: Require ALL listed category slugs (AND semantics). None = no filter.
        sort_by: One of the ``_VALID_SORT_KEYS`` values.
        user_id: When provided, populate ``is_favorite`` for each row.

    Returns:
        Pagination[PublicProblemListItem]: Paginated result with problem rows.
    """
    effective_sort = sort_by if sort_by in _VALID_SORT_KEYS else "number_asc"
    params = PaginationParams(page=max(1, page), per_page=max(1, per_page))

    solver_counts = (
        select(
            _solvers_table.c.problem_id.label("problem_id"),
            func.count(_solvers_table.c.user_id).label("solver_count"),
        )
        .join(_problems_table, _problems_table.c.id == _solvers_table.c.problem_id)
        .where(counts_toward_problem_rating(_solvers_table.c.user_id, _problems_table.c.owner_id))
        .group_by(_solvers_table.c.problem_id)
        .subquery()
    )
    solver_count = func.coalesce(solver_counts.c.solver_count, 0)
    resolved_author_name = case(
        (ArenaProblem.author_is_owner.is_(True), _users_table.c.nome),
        else_=ArenaProblem.author,
    ).label("author_name")
    base = (
        select(
            ArenaProblem,
            resolved_author_name,
            solver_count.label("solver_count"),
        )
        .outerjoin(ArenaRatingProblem, ArenaProblem.id == ArenaRatingProblem.problem_id)
        .outerjoin(_users_table, ArenaProblem.owner_id == _users_table.c.id)
        .outerjoin(solver_counts, solver_counts.c.problem_id == ArenaProblem.id)
        .options(
            contains_eager(ArenaProblem.rating),
            selectinload(ArenaProblem.categories),
            selectinload(ArenaProblem.test_cases),
        )
        .where(ArenaProblem.enabled == True)  # noqa: E712
    )

    if search.strip():
        term = f"%{search.strip()}%"
        base = base.where(
            or_(
                cast(ArenaProblem.arena_number, SAString).ilike(term),
                ArenaProblem.title.ilike(term),
                ArenaProblem.source.ilike(term),
                ArenaProblem.author.ilike(term),
                and_(ArenaProblem.author_is_owner.is_(True), _users_table.c.nome.ilike(term)),
            )
        )

    if category_slugs:
        effective_slugs = list(dict.fromkeys(slug.strip().lower() for slug in category_slugs if slug.strip()))
        if effective_slugs:
            sub = (
                select(func.count(_cat_map_table.c.category_id.distinct()))
                .select_from(_cat_map_table.join(ArenaCategory, _cat_map_table.c.category_id == ArenaCategory.id))
                .where(
                    _cat_map_table.c.problem_id == ArenaProblem.id,
                    ArenaCategory.slug.in_(effective_slugs),
                )
                .scalar_subquery()
            )
            base = base.where(sub == len(effective_slugs))

    count_stmt = select(func.count()).select_from(base.subquery())
    total: int = (await session.execute(count_stmt)).scalar_one()

    paginated = _apply_sort(base, effective_sort, solver_count).offset(params.offset).limit(params.per_page)
    rows = list((await session.execute(paginated)).unique())

    # Solver counts come from the main query because they can drive global pagination order.
    favorite_ids: set[str] = set()
    solved_ids: set[str] = set()
    if rows:
        page_problem_ids = [row[0].id for row in rows]

        if user_id:
            fav_rows = (
                await session.execute(
                    select(_favorites_table.c.problem_id).where(
                        _favorites_table.c.user_id == user_id,
                        _favorites_table.c.problem_id.in_(page_problem_ids),
                    )
                )
            ).all()
            favorite_ids = {r[0] for r in fav_rows}

            solved_rows = (
                await session.execute(
                    select(_solvers_table.c.problem_id).where(
                        _solvers_table.c.user_id == user_id,
                        _solvers_table.c.problem_id.in_(page_problem_ids),
                    )
                )
            ).all()
            solved_ids = {r[0] for r in solved_rows}

    items: list[PublicProblemListItem] = []
    for row in rows:
        problem = row[0]
        author_name = row[1]
        solver_count_value = int(row[2] or 0)
        rating = problem.rating.display_rating if problem.rating else None
        ac_rate = problem.rating.solve_rate if problem.rating else None
        items.append(
            PublicProblemListItem(
                problem=problem,
                rating=rating,
                categories=list(problem.categories),
                author_name=author_name,
                is_favorite=problem.id in favorite_ids,
                ac_rate=ac_rate,
                is_solved=problem.id in solved_ids,
                solved=solver_count_value if solver_count_value > 0 else None,
            )
        )

    return Pagination(items=items, page=params.page, per_page=params.per_page, total=total)


@dataclass(frozen=True)
class LatestProblemItem:
    """Latest (most recently created or edited) problem for dashboard display.

    Attributes:
        arena_number: Public problem number (e.g. 1042).
        title: Problem title.
        updated_at: Timestamp of the problem's last edit (equals its creation
            time until it is first edited).
    """

    arena_number: int
    title: str
    updated_at: datetime


async def get_latest_problems(
    session: AsyncSession,
    *,
    limit: int = 10,
) -> list[LatestProblemItem]:
    """Return the ``limit`` most recently created or edited enabled problems.

    Problems are ordered by ``updated_at`` descending.  Because ``updated_at``
    is initialized equal to ``created_at`` on insert and bumped on every edit,
    this ordering surfaces both newly created and recently edited problems.

    Args:
        session: Active async database session.
        limit: Maximum number of problems to return (default 10).

    Returns:
        List of ``LatestProblemItem`` ordered newest-edit first.  May contain
        fewer than ``limit`` if the total pool of enabled problems is smaller.
    """
    stmt = (
        select(ArenaProblem)
        .where(ArenaProblem.enabled == True)  # noqa: E712
        .order_by(ArenaProblem.updated_at.desc())
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).unique())
    return [
        LatestProblemItem(
            arena_number=problem.arena_number,
            title=problem.title,
            updated_at=problem.updated_at,
        )
        for problem, *_ in rows
    ]


async def get_enabled_problem_by_number(
    session: AsyncSession,
    arena_number: int,
) -> tuple[ArenaProblem, AuthorInfo] | None:
    """Fetch a single enabled problem by its public arena_number.

    Args:
        session: Active async database session.
        arena_number: Public sequential reference number.

    Returns:
        Tuple of ``(ArenaProblem, AuthorInfo)`` or ``None`` if not found / disabled.
    """
    stmt = (
        select(
            ArenaProblem,
            _users_table.c.nome.label("author_name"),
            _affiliations_table.c.name.label("affiliation_name"),
            _affiliations_table.c.country_code.label("affiliation_country_code"),
        )
        .outerjoin(ArenaRatingProblem, ArenaProblem.id == ArenaRatingProblem.problem_id)
        .outerjoin(_users_table, ArenaProblem.owner_id == _users_table.c.id)
        .outerjoin(
            _affiliations_table,
            _affiliations_table.c.id == _users_table.c.affiliation_id,
        )
        .options(
            contains_eager(ArenaProblem.rating),
            selectinload(ArenaProblem.categories),
            selectinload(ArenaProblem.test_cases),
        )
        .where(ArenaProblem.arena_number == arena_number, ArenaProblem.enabled == True)  # noqa: E712
    )
    row = (await session.execute(stmt)).unique().one_or_none()
    if row is None:
        return None
    problem, owner_name, affiliation_name, affiliation_country_code = row
    if not problem.author_is_owner:
        return problem, AuthorInfo(
            name=problem.author,
            affiliation_name=None,
            affiliation_country_code=None,
        )
    return problem, AuthorInfo(
        name=owner_name,
        affiliation_name=affiliation_name,
        affiliation_country_code=affiliation_country_code,
    )


async def get_all_categories(session: AsyncSession) -> list[ArenaCategory]:
    """Return all categories ordered by name, for the filter dropdown.

    Args:
        session: Active async database session.

    Returns:
        list[ArenaCategory]: All categories sorted alphabetically by name.
    """
    result = await session.execute(select(ArenaCategory).order_by(func.lower(ArenaCategory.name)))
    return list(result.scalars())


async def get_user_problem_status(
    session: AsyncSession,
    *,
    user_id: str,
    problem_id: str,
) -> tuple[datetime | None, datetime | None, bool]:
    """Return the solved_at, tried_at, and is_favorite status for a user–problem pair.

    Args:
        session: Active async database session.
        user_id: UUID of the Arena user.
        problem_id: UUID of the ArenaProblem.

    Returns:
        Tuple of ``(solved_at, tried_at, is_favorite)`` where datetimes may be
        ``None`` if the user has no record for that problem.
    """
    solved_row = (
        await session.execute(
            select(_solvers_table.c.solved_at).where(
                _solvers_table.c.user_id == user_id,
                _solvers_table.c.problem_id == problem_id,
            )
        )
    ).one_or_none()

    tried_row = (
        await session.execute(
            select(_tried_table.c.last_tried_at).where(
                _tried_table.c.user_id == user_id,
                _tried_table.c.problem_id == problem_id,
            )
        )
    ).one_or_none()

    fav_row = (
        await session.execute(
            select(_favorites_table.c.problem_id).where(
                _favorites_table.c.user_id == user_id,
                _favorites_table.c.problem_id == problem_id,
            )
        )
    ).one_or_none()

    solved_at = solved_row[0] if solved_row else None
    tried_at = tried_row[0] if tried_row else None
    return solved_at, tried_at, fav_row is not None


async def get_adjacent_problem_numbers(
    session: AsyncSession,
    arena_number: int,
) -> tuple[int | None, int | None]:
    """Return the arena_number of the nearest enabled problems before and after the given one.

    Args:
        session: Active async database session.
        arena_number: The current problem's public arena number.

    Returns:
        Tuple of ``(prev_number, next_number)`` where either may be ``None``
        if no enabled problem exists in that direction.
    """
    prev_row = (
        await session.execute(
            select(ArenaProblem.arena_number)
            .where(ArenaProblem.enabled == True, ArenaProblem.arena_number < arena_number)  # noqa: E712
            .order_by(ArenaProblem.arena_number.desc())
            .limit(1)
        )
    ).one_or_none()

    next_row = (
        await session.execute(
            select(ArenaProblem.arena_number)
            .where(ArenaProblem.enabled == True, ArenaProblem.arena_number > arena_number)  # noqa: E712
            .order_by(ArenaProblem.arena_number.asc())
            .limit(1)
        )
    ).one_or_none()

    return (prev_row[0] if prev_row else None, next_row[0] if next_row else None)


async def get_problem_rating_history(
    session: AsyncSession,
    problem_id: str,
) -> list[dict[str, object]]:
    """Return rating history for a problem for the last 24 months.

    Rating values are converted from the internal [1, 100] scale to the
    user-facing display scale [0.1, 10.0] by dividing by 10.

    Args:
        session: Active async database session.
        problem_id: UUID of the ArenaProblem.

    Returns:
        list[dict]: Each item has ``ts`` (ISO-8601 string) and ``rating`` (float).
    """
    cutoff = datetime.now(tz=UTC) - timedelta(days=730)
    stmt = (
        select(
            arena_problem_rating_history.c.computed_at,
            arena_problem_rating_history.c.rating,
        )
        .where(
            arena_problem_rating_history.c.problem_id == problem_id,
            arena_problem_rating_history.c.computed_at >= cutoff,
        )
        .order_by(arena_problem_rating_history.c.computed_at.asc())
    )
    rows = (await session.execute(stmt)).fetchall()
    return [{"ts": row.computed_at.isoformat(), "rating": row.rating / 10.0} for row in rows]
