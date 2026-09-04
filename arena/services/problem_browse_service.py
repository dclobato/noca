#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
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

from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from arena.models.arena_problems import ArenaCategory, ArenaProblem, ArenaRatingProblem
from arena.services.pagination_service import Pagination, PaginationParams, clamp_page
from arena.services.problem_list_query_service import ProblemListCategory, categories_by_problem_id
from arena.services.problem_search_service import prepare_problem_search
from shared.db_schema.arena import arena_affiliations as _affiliations_table
from shared.db_schema.arena import arena_problem_category_map as _cat_map_table
from shared.db_schema.arena import arena_problem_favorites as _favorites_table
from shared.db_schema.arena import arena_problem_solvers as _solvers_table
from shared.db_schema.arena import arena_problem_tried as _tried_table
from shared.db_schema.arena import arena_problems as _problems_table
from shared.db_schema.arena import arena_users as _users_table
from shared.db_schema.arena.arena_rating_history import arena_problem_rating_history
from shared.enumerations import ProblemValidatorType, StatementLanguage
from shared.services.arena_difficulty_display import DifficultyDisplay, difficulty_display
from shared.services.arena_query_helpers import counts_toward_problem_rating


@dataclass(frozen=True)
class AuthorInfo:
    """Display information about a problem's author for the detail page.

    Attributes:
        name: Author's full display name, or ``None`` if the author record is missing.
        affiliation_name: Name of the author's affiliated institution, or ``None``.
        affiliation_country_code: ISO 3166-1 alpha-2 code for the affiliation's country, or ``None``.
        affiliation_subdivision_code: ISO 3166-2 code for the affiliation's
            subdivision, or ``None``.
    """

    name: str | None
    affiliation_name: str | None
    affiliation_country_code: str | None
    affiliation_subdivision_code: str | None


# Single source of truth for the public problem-list sort contract; the public
# route imports these rather than restating them.
DEFAULT_SORT = "number_asc"
RELEVANCE_SORT = "relevance"
VALID_SORTS = frozenset(
    {
        RELEVANCE_SORT,
        "title_asc",
        "title_desc",
        "number_asc",
        "number_desc",
        "rating_asc",
        "rating_desc",
        "solvers_asc",
        "solvers_desc",
    }
)

_PUBLIC_PER_PAGE = 25


@dataclass(frozen=True)
class PublicProblemListItem:
    """Single row in the public problem list page.

    Attributes:
        id: Problem UUID.
        arena_number: Sequential public problem number.
        title: Problem title.
        difficulty: Evidence-gated difficulty presentation (measured value or
            the unknown state when too few users have attempted the problem).
        categories: Categories linked to this problem.
        author_name: Display name of the problem author, or ``None`` if missing.
        is_favorite: Whether the viewing user has favorited this problem.
        ac_rate: Fraction of users who solved the problem (0.0–1.0), or ``None``
            if no rating data is available yet.
        is_solved: Whether the viewing user has a first-AC record for this problem.
        solved: Number of distinct non-owner solvers for this problem, or
            ``None`` if no counted solver data is available.
        has_custom_validator: Whether the problem's stored strategy is interactive.
    """

    id: str
    arena_number: int
    title: str
    difficulty: DifficultyDisplay
    categories: list[ProblemListCategory]
    author_name: str | None
    is_favorite: bool = False
    ac_rate: float | None = None
    is_solved: bool = False
    solved: int | None = None
    has_custom_validator: bool = False


def _apply_sort(
    stmt: Select[Any],
    sort_by: str,
    solver_count: Any,
    relevance: Any | None = None,
) -> Select[Any]:
    """Append ORDER BY clause for the given sort key.

    Args:
        stmt: Base select statement.
        sort_by: One of the ``VALID_SORTS`` values.
        solver_count: SQL expression with the live participant solver count.

    Returns:
        Select: The statement with an ORDER BY clause appended.
    """
    if sort_by == RELEVANCE_SORT and relevance is not None:
        return stmt.order_by(
            relevance.c.exact_number_match.desc(),
            relevance.c.full_text_match.desc(),
            relevance.c.full_text_rank.desc(),
            relevance.c.trigram_rank.desc(),
            ArenaProblem.arena_number.asc(),
        )
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
    language: StatementLanguage | None = None,
    sort_by: str = "",
    user_id: str | None = None,
) -> Pagination[PublicProblemListItem]:
    """Return a paginated list of enabled problems with search and filter support.

    Search covers arena number, title, statement, source, and the resolved author name.
    Category filter uses OR semantics: a problem may belong to any selected category.

    Args:
        session: Active async database session.
        page: 1-based page number.
        per_page: Number of items per page (default 25).
        search: Hybrid search applied to number, title, statement, source, and author name.
        category_slugs: Require ANY listed category slug (OR semantics). None = no filter.
        language: Restrict to problems whose statement is in this language. None = no filter.
        sort_by: One of the ``VALID_SORTS`` values.
        user_id: When provided, populate ``is_favorite`` for each row.

    Returns:
        Pagination[PublicProblemListItem]: Paginated result with problem rows.
    """
    normalized_search = search.strip()
    default_sort = RELEVANCE_SORT if normalized_search else DEFAULT_SORT
    effective_sort = sort_by if sort_by in VALID_SORTS else default_sort
    if effective_sort == RELEVANCE_SORT and not normalized_search:
        effective_sort = DEFAULT_SORT
    params = PaginationParams(page=max(1, page), per_page=max(1, per_page))

    # Keep this statement filter-only. The count must not inherit display
    # joins, aggregates, or correlated projections.
    filtered_problem_ids = select(ArenaProblem.id).where(ArenaProblem.enabled.is_(True))

    if language is not None:
        filtered_problem_ids = filtered_problem_ids.where(ArenaProblem.statement_language == language)

    if normalized_search:
        search_expressions = await prepare_problem_search(session, normalized_search)
        filtered_problem_ids = (
            filtered_problem_ids.outerjoin(
                _users_table,
                ArenaProblem.owner_id == _users_table.c.id,
            )
            .add_columns(
                search_expressions.exact_number_match.label("exact_number_match"),
                search_expressions.full_text_match.label("full_text_match"),
                search_expressions.full_text_rank.label("full_text_rank"),
                search_expressions.trigram_rank.label("trigram_rank"),
            )
            .where(search_expressions.predicate)
        )

    if category_slugs:
        effective_slugs = list(dict.fromkeys(slug.strip().lower() for slug in category_slugs if slug.strip()))
        if effective_slugs:
            # OR semantics: one matching category row is enough to include the problem.
            matching_category = (
                select(_cat_map_table.c.category_id)
                .select_from(_cat_map_table.join(ArenaCategory, _cat_map_table.c.category_id == ArenaCategory.id))
                .where(
                    _cat_map_table.c.problem_id == ArenaProblem.id,
                    ArenaCategory.slug.in_(effective_slugs),
                )
            )
            filtered_problem_ids = filtered_problem_ids.where(matching_category.exists())

    count_stmt = select(func.count()).select_from(filtered_problem_ids.subquery())
    total: int = (await session.execute(count_stmt)).scalar_one()

    # Clamp to the pages that actually exist before the offset is computed, as
    # every other paginated service here does.  Without it a large page number
    # becomes an out-of-range SQL OFFSET, which PostgreSQL rejects and which
    # surfaces as a 503 rather than an empty last page.
    params = PaginationParams(
        page=clamp_page(params.page, total=total, per_page=params.per_page),
        per_page=params.per_page,
    )

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
    # The public "interactive problem" marker follows the stored strategy: a
    # problem whose validator source was removed is still interactive, and a
    # stale validator row never makes a standard problem look like one.
    has_custom_validator = (ArenaProblem.validator_type == ProblemValidatorType.INTERACTIVE).label(
        "has_custom_validator"
    )
    filtered_ids = filtered_problem_ids.subquery()
    display_statement = (
        select(
            ArenaProblem.id,
            ArenaProblem.arena_number,
            ArenaProblem.title,
            resolved_author_name,
            solver_count.label("solver_count"),
            ArenaRatingProblem.rating.label("rating_value"),
            ArenaRatingProblem.attempted_users,
            ArenaRatingProblem.solved_users,
            ArenaProblem.expected_difficulty,
            has_custom_validator,
        )
        .join(filtered_ids, filtered_ids.c.id == ArenaProblem.id)
        .outerjoin(ArenaRatingProblem, ArenaProblem.id == ArenaRatingProblem.problem_id)
        .outerjoin(_users_table, ArenaProblem.owner_id == _users_table.c.id)
        .outerjoin(solver_counts, solver_counts.c.problem_id == ArenaProblem.id)
    )
    paginated = (
        _apply_sort(
            display_statement,
            effective_sort,
            solver_count,
            filtered_ids if normalized_search else None,
        )
        .offset(params.offset)
        .limit(params.per_page)
    )
    # Every display join is one-to-one or grouped by problem, so the statement
    # cannot fan out and Result.unique() would only conceal a future bad join.
    rows = list((await session.execute(paginated)).all())
    page_problem_ids = [row.id for row in rows]
    categories = await categories_by_problem_id(session, page_problem_ids)

    # Solver counts come from the main query because they can drive global pagination order.
    favorite_ids: set[str] = set()
    solved_ids: set[str] = set()
    if rows and user_id:
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
        solver_count_value = int(row.solver_count or 0)
        if row.rating_value is None:
            ac_rate = None
        else:
            ac_rate = row.solved_users / row.attempted_users if row.attempted_users else 0.0
        items.append(
            PublicProblemListItem(
                id=row.id,
                arena_number=row.arena_number,
                title=row.title,
                difficulty=difficulty_display(row.rating_value, row.attempted_users, row.expected_difficulty),
                categories=categories.get(row.id, []),
                author_name=row.author_name,
                is_favorite=row.id in favorite_ids,
                ac_rate=ac_rate,
                is_solved=row.id in solved_ids,
                solved=solver_count_value if solver_count_value > 0 else None,
                has_custom_validator=row.has_custom_validator,
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
        select(
            ArenaProblem.arena_number,
            ArenaProblem.title,
            ArenaProblem.updated_at,
        )
        .where(ArenaProblem.enabled.is_(True))
        .order_by(ArenaProblem.updated_at.desc())
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).all())
    return [
        LatestProblemItem(
            arena_number=row.arena_number,
            title=row.title,
            updated_at=row.updated_at,
        )
        for row in rows
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
            _affiliations_table.c.subdivision_code.label("affiliation_subdivision_code"),
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
            selectinload(ArenaProblem.custom_validator),
        )
        .where(ArenaProblem.arena_number == arena_number, ArenaProblem.enabled == True)  # noqa: E712
    )
    row = (await session.execute(stmt)).unique().one_or_none()
    if row is None:
        return None
    (
        problem,
        owner_name,
        affiliation_name,
        affiliation_country_code,
        affiliation_subdivision_code,
    ) = row
    if not problem.author_is_owner:
        return problem, AuthorInfo(
            name=problem.author,
            affiliation_name=None,
            affiliation_country_code=None,
            affiliation_subdivision_code=None,
        )
    return problem, AuthorInfo(
        name=owner_name,
        affiliation_name=affiliation_name,
        affiliation_country_code=affiliation_country_code,
        affiliation_subdivision_code=affiliation_subdivision_code,
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
