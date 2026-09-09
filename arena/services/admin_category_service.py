#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Admin-facing Arena category management service.

Field rules (name, slug, badge color) are shared with collections and live in
:mod:`arena.services.taxonomy_validation`. ``normalize_slug`` is re-exported
here because callers outside this module already import it from this path.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import ArenaCategory
from arena.services.pagination_service import Pagination, PaginationParams
from arena.services.taxonomy_validation import (
    normalize_slug,
    validate_color,
    validate_required_text,
    validate_slug,
)
from shared.db_schema.arena import arena_problem_category_map

__all__ = [
    "DEFAULT_SORT",
    "VALID_SORTS",
    "CategoryFormData",
    "CategoryListItem",
    "create_category",
    "delete_category",
    "get_category",
    "get_problem_count",
    "list_categories_paginated",
    "normalize_slug",
    "update_category",
    "validate_category_data",
]

DEFAULT_SORT = "name_asc"
VALID_SORTS = frozenset({"name_asc", "name_desc", "problems_asc", "problems_desc"})


@dataclass(frozen=True)
class CategoryFormData:
    """Normalized category form data ready for persistence."""

    name: str
    slug: str
    color: str


@dataclass(frozen=True)
class CategoryListItem:
    """Arena category row with precomputed problem count for list pages."""

    category: ArenaCategory
    problem_count: int


async def _ensure_name_unique(session: AsyncSession, name: str, *, exclude_id: str | None = None) -> None:
    """Raise ValueError when another category already uses this name, ignoring case."""
    query = select(ArenaCategory.id).where(func.lower(ArenaCategory.name) == name.lower())
    if exclude_id is not None:
        query = query.where(ArenaCategory.id != exclude_id)
    if (await session.execute(query)).scalar_one_or_none() is not None:
        raise ValueError("A category with this name already exists.")


async def _ensure_slug_unique(session: AsyncSession, slug: str, *, exclude_id: str | None = None) -> None:
    """Raise ValueError when another category already uses this normalized slug."""
    query = select(ArenaCategory.id).where(ArenaCategory.slug == slug)
    if exclude_id is not None:
        query = query.where(ArenaCategory.id != exclude_id)
    if (await session.execute(query)).scalar_one_or_none() is not None:
        raise ValueError("A category with this slug already exists.")


async def validate_category_data(
    session: AsyncSession,
    *,
    name: str,
    slug: str,
    color: str,
    exclude_id: str | None = None,
) -> CategoryFormData:
    """Validate and normalize category form input.

    Args:
        session: Active async database session.
        name: Raw category name.
        slug: Raw category slug.
        color: Raw badge color.
        exclude_id: Existing category ID to ignore during uniqueness checks.

    Returns:
        CategoryFormData: Normalized data suitable for persistence.

    Raises:
        ValueError: If any field is invalid or not unique.
    """
    normalized_name = validate_required_text(name, "Name")
    normalized_slug = validate_slug(slug)
    normalized_color = validate_color(color)

    await _ensure_name_unique(session, normalized_name, exclude_id=exclude_id)
    await _ensure_slug_unique(session, normalized_slug, exclude_id=exclude_id)
    return CategoryFormData(name=normalized_name, slug=normalized_slug, color=normalized_color)


async def list_categories_paginated(
    session: AsyncSession,
    *,
    page: int,
    per_page: int,
    sort_by: str = DEFAULT_SORT,
    search: str | None = None,
) -> Pagination[CategoryListItem]:
    """Return categories with linked problem counts, ordered by the requested column.

    Args:
        session: Active async database session.
        page: 1-based page number.
        per_page: Rows per page.
        sort_by: One of ``name_asc``, ``name_desc``, ``problems_asc``,
            ``problems_desc``. Falls back to ``name_asc`` for unknown values.
        search: Optional name filter (case-insensitive substring match).

    Returns:
        Pagination[CategoryListItem]: Paginated category rows.
    """
    effective_sort = sort_by if sort_by in VALID_SORTS else DEFAULT_SORT
    params = PaginationParams(page=page, per_page=per_page)

    normalized_search = search.strip() if search and search.strip() else None
    count_q = select(func.count()).select_from(ArenaCategory)
    if normalized_search:
        count_q = count_q.where(ArenaCategory.name.ilike(f"%{normalized_search}%"))
    total = (await session.execute(count_q)).scalar() or 0

    count_label = func.count(arena_problem_category_map.c.problem_id).label("problem_count")
    name_lower = func.lower(ArenaCategory.name)

    if effective_sort == "name_desc":
        order_clauses = (name_lower.desc(), ArenaCategory.name.desc())
    elif effective_sort == "problems_asc":
        order_clauses = (count_label.asc(), name_lower.asc())
    elif effective_sort == "problems_desc":
        order_clauses = (count_label.desc(), name_lower.asc())
    else:
        order_clauses = (name_lower.asc(), ArenaCategory.name.asc())

    query: Select[tuple[ArenaCategory, int]] = (
        select(ArenaCategory, count_label)
        .outerjoin(
            arena_problem_category_map,
            ArenaCategory.id == arena_problem_category_map.c.category_id,
        )
        .group_by(ArenaCategory.id)
        .order_by(*order_clauses)
        .offset(params.offset)
        .limit(params.per_page)
    )
    if normalized_search:
        query = query.where(ArenaCategory.name.ilike(f"%{normalized_search}%"))
    rows = (await session.execute(query)).all()
    items = [CategoryListItem(category=category, problem_count=problem_count) for category, problem_count in rows]
    return Pagination(items=items, page=params.page, per_page=params.per_page, total=total)


async def get_category(session: AsyncSession, category_id: str) -> ArenaCategory | None:
    """Fetch an Arena category by ID."""
    return await session.get(ArenaCategory, category_id)


async def get_problem_count(session: AsyncSession, category_id: str) -> int:
    """Return the number of problems linked to a category."""
    query = (
        select(func.count())
        .select_from(arena_problem_category_map)
        .where(arena_problem_category_map.c.category_id == category_id)
    )
    return (await session.execute(query)).scalar() or 0


async def create_category(
    session: AsyncSession,
    *,
    name: str,
    slug: str,
    color: str,
) -> ArenaCategory:
    """Create and flush an Arena category after validation."""
    data = await validate_category_data(session, name=name, slug=slug, color=color)
    category = ArenaCategory(name=data.name, slug=data.slug, color=data.color)
    session.add(category)
    await session.flush()
    return category


async def update_category(
    session: AsyncSession,
    category: ArenaCategory,
    *,
    name: str,
    slug: str,
    color: str,
) -> ArenaCategory:
    """Update and flush an Arena category after validation."""
    data = await validate_category_data(session, name=name, slug=slug, color=color, exclude_id=category.id)
    category.name = data.name
    category.slug = data.slug
    category.color = data.color
    await session.flush()
    return category


async def delete_category(session: AsyncSession, category: ArenaCategory) -> None:
    """Delete an Arena category; problem links are removed by FK cascade."""
    await session.delete(category)
    await session.flush()
