#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Admin-facing Arena collection management service.

A collection is an event (ICPC, Maratona SBC, InterIF) or a class (Iniciantes,
Expressoes regulares). A problem belongs to at most one, so the link is the
nullable ``arena_problems.collection_id`` column rather than a junction table.

Slug normalization and the field-level rules come from
:mod:`arena.services.taxonomy_validation`, shared with categories so the two
taxonomies cannot drift apart on stop words or slug shape. A collection has
no badge color: it is a name and a slug.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import ArenaCollection, ArenaProblem
from arena.services.pagination_service import Pagination, PaginationParams
from arena.services.taxonomy_validation import validate_required_text, validate_slug

DEFAULT_SORT = "name_asc"
VALID_SORTS = frozenset({"name_asc", "name_desc", "problems_asc", "problems_desc"})


@dataclass(frozen=True)
class CollectionFormData:
    """Normalized collection form data ready for persistence."""

    name: str
    slug: str


@dataclass(frozen=True)
class CollectionListItem:
    """Arena collection row with precomputed problem count for list pages."""

    collection: ArenaCollection
    problem_count: int


async def _ensure_name_unique(session: AsyncSession, name: str, *, exclude_id: str | None = None) -> None:
    """Raise ValueError when another collection already uses this name, ignoring case."""
    query = select(ArenaCollection.id).where(func.lower(ArenaCollection.name) == name.lower())
    if exclude_id is not None:
        query = query.where(ArenaCollection.id != exclude_id)
    if (await session.execute(query)).scalar_one_or_none() is not None:
        raise ValueError("A collection with this name already exists.")


async def _ensure_slug_unique(session: AsyncSession, slug: str, *, exclude_id: str | None = None) -> None:
    """Raise ValueError when another collection already uses this normalized slug."""
    query = select(ArenaCollection.id).where(ArenaCollection.slug == slug)
    if exclude_id is not None:
        query = query.where(ArenaCollection.id != exclude_id)
    if (await session.execute(query)).scalar_one_or_none() is not None:
        raise ValueError("A collection with this slug already exists.")


async def validate_collection_data(
    session: AsyncSession,
    *,
    name: str,
    slug: str,
    exclude_id: str | None = None,
) -> CollectionFormData:
    """Validate and normalize collection form input.

    Args:
        session: Active async database session.
        name: Raw collection name.
        slug: Raw collection slug.
        exclude_id: Existing collection ID to ignore during uniqueness checks.

    Returns:
        CollectionFormData: Normalized data suitable for persistence.

    Raises:
        ValueError: If any field is invalid or not unique.
    """
    normalized_name = validate_required_text(name, "Name")
    normalized_slug = validate_slug(slug)

    await _ensure_name_unique(session, normalized_name, exclude_id=exclude_id)
    await _ensure_slug_unique(session, normalized_slug, exclude_id=exclude_id)
    return CollectionFormData(name=normalized_name, slug=normalized_slug)


async def list_collections(session: AsyncSession) -> list[ArenaCollection]:
    """Return every collection ordered by name, for pickers and filter dropdowns."""
    query = select(ArenaCollection).order_by(func.lower(ArenaCollection.name))
    return list((await session.execute(query)).scalars().all())


async def list_collections_paginated(
    session: AsyncSession,
    *,
    page: int,
    per_page: int,
    sort_by: str = DEFAULT_SORT,
    search: str | None = None,
) -> Pagination[CollectionListItem]:
    """Return collections with linked problem counts, ordered by the requested column.

    Args:
        session: Active async database session.
        page: 1-based page number.
        per_page: Rows per page.
        sort_by: One of ``name_asc``, ``name_desc``, ``problems_asc``,
            ``problems_desc``. Falls back to ``name_asc`` for unknown values.
        search: Optional name filter (case-insensitive substring match).

    Returns:
        Pagination[CollectionListItem]: Paginated collection rows.
    """
    effective_sort = sort_by if sort_by in VALID_SORTS else DEFAULT_SORT
    params = PaginationParams(page=page, per_page=per_page)

    normalized_search = search.strip() if search and search.strip() else None
    count_q = select(func.count()).select_from(ArenaCollection)
    if normalized_search:
        count_q = count_q.where(ArenaCollection.name.ilike(f"%{normalized_search}%"))
    total = (await session.execute(count_q)).scalar() or 0

    count_label = func.count(ArenaProblem.id).label("problem_count")
    name_lower = func.lower(ArenaCollection.name)

    if effective_sort == "name_desc":
        order_clauses = (name_lower.desc(), ArenaCollection.name.desc())
    elif effective_sort == "problems_asc":
        order_clauses = (count_label.asc(), name_lower.asc())
    elif effective_sort == "problems_desc":
        order_clauses = (count_label.desc(), name_lower.asc())
    else:
        order_clauses = (name_lower.asc(), ArenaCollection.name.asc())

    query: Select[tuple[ArenaCollection, int]] = (
        select(ArenaCollection, count_label)
        .outerjoin(ArenaProblem, ArenaProblem.collection_id == ArenaCollection.id)
        .group_by(ArenaCollection.id)
        .order_by(*order_clauses)
        .offset(params.offset)
        .limit(params.per_page)
    )
    if normalized_search:
        query = query.where(ArenaCollection.name.ilike(f"%{normalized_search}%"))
    rows = (await session.execute(query)).all()
    items = [
        CollectionListItem(collection=collection, problem_count=problem_count) for collection, problem_count in rows
    ]
    return Pagination(items=items, page=params.page, per_page=params.per_page, total=total)


async def get_collection(session: AsyncSession, collection_id: str) -> ArenaCollection | None:
    """Fetch an Arena collection by ID."""
    return await session.get(ArenaCollection, collection_id)


async def get_collection_by_slug(session: AsyncSession, slug: str) -> ArenaCollection | None:
    """Fetch an Arena collection by its normalized slug.

    Args:
        session: Active async database session.
        slug: Raw slug from a query string; stripped and lowercased here.

    Returns:
        ArenaCollection | None: The collection, or ``None`` when the slug is
        blank or matches nothing.
    """
    normalized = slug.strip().lower()
    if not normalized:
        return None
    query = select(ArenaCollection).where(ArenaCollection.slug == normalized)
    return (await session.execute(query)).scalar_one_or_none()


async def get_problem_count(session: AsyncSession, collection_id: str) -> int:
    """Return the number of problems filed under a collection."""
    query = select(func.count()).select_from(ArenaProblem).where(ArenaProblem.collection_id == collection_id)
    return (await session.execute(query)).scalar() or 0


async def create_collection(
    session: AsyncSession,
    *,
    name: str,
    slug: str,
) -> ArenaCollection:
    """Create and flush an Arena collection after validation."""
    data = await validate_collection_data(session, name=name, slug=slug)
    collection = ArenaCollection(name=data.name, slug=data.slug)
    session.add(collection)
    await session.flush()
    return collection


async def update_collection(
    session: AsyncSession,
    collection: ArenaCollection,
    *,
    name: str,
    slug: str,
) -> ArenaCollection:
    """Update and flush an Arena collection after validation."""
    data = await validate_collection_data(session, name=name, slug=slug, exclude_id=collection.id)
    collection.name = data.name
    collection.slug = data.slug
    await session.flush()
    return collection


async def delete_collection(session: AsyncSession, collection: ArenaCollection) -> None:
    """Delete an Arena collection; its problems are unfiled by the SET NULL FK."""
    await session.delete(collection)
    await session.flush()
