#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Admin-facing Arena category management service."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import ArenaCategory
from arena.services.pagination_service import Pagination, PaginationParams
from shared.db_schema.arena import arena_problem_category_map

_MAX_FIELD_LENGTH = 128
_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Stop words removed before building a slug so that prepositions and articles
# do not inflate URL length.  The set covers common Portuguese function words
# plus their direct English equivalents (useful for mixed-language category names).
_SLUG_STOP_WORDS: frozenset[str] = frozenset(
    {
        # Portuguese articles
        "a",
        "o",
        "as",
        "os",
        "um",
        "uma",
        # Portuguese prepositions & contractions
        "de",
        "do",
        "da",
        "dos",
        "das",
        "em",
        "no",
        "na",
        "nos",
        "nas",
        "por",
        "para",
        "com",
        "pelo",
        "pela",
        "pelos",
        "pelas",
        # Portuguese conjunctions / pronouns
        "e",
        "ou",
        "se",
        # English articles / prepositions / conjunctions
        "the",
        "an",
        "and",
        "or",
        "of",
        "in",
        "on",
        "for",
        "to",
        "from",
        "with",
        "by",
        "at",
        # English copula
        "is",
        "are",
    }
)

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


def normalize_slug(value: str) -> str:
    """Normalize text into the same lowercase hyphen slug used by contest forms.

    Diacritics are stripped, stop words (Portuguese + English) are removed, and
    the remaining tokens are joined with hyphens.  This keeps slugs concise while
    preserving all semantically meaningful words.

    Args:
        value: Raw slug or human-readable name.

    Returns:
        str: URL-safe, stop-word-free slug.
    """
    normalized = unicodedata.normalize("NFD", value.lower())
    without_marks = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    tokens = re.sub(r"[^a-z0-9]+", " ", without_marks).split()
    words = [t for t in tokens if t not in _SLUG_STOP_WORDS]
    return "-".join(words)


def _validate_required_text(value: str, field_name: str) -> str:
    """Strip and validate a required 128-character category field."""
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} is required.")
    if len(stripped) > _MAX_FIELD_LENGTH:
        raise ValueError(f"{field_name} must be at most {_MAX_FIELD_LENGTH} characters.")
    return stripped


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
    normalized_name = _validate_required_text(name, "Name")
    normalized_slug = normalize_slug(_validate_required_text(slug, "Slug"))
    if len(normalized_slug) > _MAX_FIELD_LENGTH:
        raise ValueError(f"Slug must be at most {_MAX_FIELD_LENGTH} characters.")
    if not _SLUG_PATTERN.fullmatch(normalized_slug):
        raise ValueError("Slug must contain lowercase letters, numbers, and single hyphens only.")

    normalized_color = _validate_required_text(color, "Color").lower()
    if not _COLOR_PATTERN.fullmatch(normalized_color):
        raise ValueError("Color must be a 6-digit hex value like #6c757d.")

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
