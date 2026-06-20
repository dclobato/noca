#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Admin-facing Arena affiliation management service.

Covers paginated listing with name/country/subdivision filters, create,
update, and delete (with explicit user-detach before removal).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from arena.models.arena_affiliations import ArenaAffiliation
from arena.models.arena_users import ArenaUser
from arena.services.pagination_service import Pagination, PaginationParams
from arena.services.profile_location_service import validate_country_code, validate_subdivision_code

_MAX_NAME_LENGTH = 200
_MAX_URL_LENGTH = 500
_ALLOWED_PER_PAGE = frozenset({10, 25, 50, 100})
DEFAULT_PER_PAGE = 25


@dataclass(frozen=True)
class AffiliationFormData:
    """Normalized affiliation form data ready for persistence."""

    name: str
    url: str | None
    country_code: str | None
    subdivision_code: str | None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_name(value: str) -> str:
    """Strip and validate a required affiliation name."""
    stripped = value.strip()
    if not stripped:
        raise ValueError("Name is required.")
    if len(stripped) > _MAX_NAME_LENGTH:
        raise ValueError(f"Name must be at most {_MAX_NAME_LENGTH} characters.")
    return stripped


def _validate_url(value: str | None) -> str | None:
    """Strip and validate an optional affiliation URL."""
    if not value or not value.strip():
        return None
    stripped = value.strip()
    if len(stripped) > _MAX_URL_LENGTH:
        raise ValueError(f"URL must be at most {_MAX_URL_LENGTH} characters.")
    if not (stripped.startswith("http://") or stripped.startswith("https://")):
        raise ValueError("URL must start with http:// or https://.")
    return stripped


async def _ensure_name_unique(
    session: AsyncSession,
    name: str,
    *,
    exclude_id: str | None = None,
) -> None:
    """Raise ValueError when another affiliation already uses this name (case-insensitive)."""
    query = select(ArenaAffiliation.id).where(func.lower(ArenaAffiliation.name) == name.lower())
    if exclude_id is not None:
        query = query.where(ArenaAffiliation.id != exclude_id)
    if (await session.execute(query)).scalar_one_or_none() is not None:
        raise ValueError("An affiliation with this name already exists.")


async def validate_affiliation_data(
    session: AsyncSession,
    *,
    name: str,
    url: str | None,
    country_code: str | None,
    subdivision_code: str | None,
    exclude_id: str | None = None,
) -> AffiliationFormData:
    """Validate and normalize affiliation form input.

    Args:
        session: Active async database session.
        name: Raw affiliation name.
        url: Optional raw URL.
        country_code: Optional ISO 3166-1 alpha-2 country code.
        subdivision_code: Optional ISO 3166-2 subdivision code.
        exclude_id: Existing affiliation ID to ignore during uniqueness checks.

    Returns:
        AffiliationFormData: Normalized data ready for persistence.

    Raises:
        ValueError: If any field is invalid or name is not unique.
    """
    normalized_name = _validate_name(name)
    normalized_url = _validate_url(url)
    normalized_country = validate_country_code(country_code)
    normalized_subdivision = validate_subdivision_code(normalized_country, subdivision_code)
    await _ensure_name_unique(session, normalized_name, exclude_id=exclude_id)
    return AffiliationFormData(
        name=normalized_name,
        url=normalized_url,
        country_code=normalized_country,
        subdivision_code=normalized_subdivision,
    )


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def effective_per_page(value: int | None) -> int:
    """Return an allowed page size, falling back to the default."""
    if value in _ALLOWED_PER_PAGE:
        return int(value)
    return DEFAULT_PER_PAGE


async def list_affiliations_paginated(
    session: AsyncSession,
    *,
    page: int,
    per_page: int,
    search: str | None = None,
    country_code: str | None = None,
    subdivision_code: str | None = None,
) -> Pagination[ArenaAffiliation]:
    """Return affiliations matching the given filters, ordered by name.

    Args:
        session: Active async database session.
        page: 1-based page number.
        per_page: Rows per page (must be one of 10, 25, 50, 100).
        search: Optional case-insensitive name search fragment.
        country_code: Optional ISO country code filter.
        subdivision_code: Optional ISO subdivision code filter (only applied
            when country_code is also provided).

    Returns:
        Pagination[ArenaAffiliation]: Paginated affiliation rows.
    """
    params = PaginationParams(page=page, per_page=per_page)

    base_query = select(ArenaAffiliation)
    count_query = select(func.count()).select_from(ArenaAffiliation)

    if search and search.strip():
        like_fragment = f"%{search.strip().lower()}%"
        base_query = base_query.where(func.lower(ArenaAffiliation.name).like(like_fragment))
        count_query = count_query.where(func.lower(ArenaAffiliation.name).like(like_fragment))

    if country_code and country_code.strip():
        base_query = base_query.where(ArenaAffiliation.country_code == country_code.strip().upper())
        count_query = count_query.where(ArenaAffiliation.country_code == country_code.strip().upper())
        if subdivision_code and subdivision_code.strip():
            base_query = base_query.where(ArenaAffiliation.subdivision_code == subdivision_code.strip().upper())
            count_query = count_query.where(ArenaAffiliation.subdivision_code == subdivision_code.strip().upper())

    total = (await session.execute(count_query)).scalar() or 0
    name_lower = func.lower(ArenaAffiliation.name)
    rows = (
        (
            await session.execute(
                base_query.order_by(name_lower.asc(), ArenaAffiliation.name.asc())
                .offset(params.offset)
                .limit(params.per_page)
            )
        )
        .scalars()
        .all()
    )

    return Pagination(items=list(rows), page=params.page, per_page=params.per_page, total=total)


async def get_affiliation(session: AsyncSession, affiliation_id: str) -> ArenaAffiliation | None:
    """Fetch an Arena affiliation by ID."""
    return await session.get(ArenaAffiliation, affiliation_id)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def create_affiliation(
    session: AsyncSession,
    *,
    name: str,
    url: str | None,
    country_code: str | None,
    subdivision_code: str | None,
) -> ArenaAffiliation:
    """Create and flush a new Arena affiliation after validation.

    Args:
        session: Active async database session.
        name: Affiliation display name.
        url: Optional public URL.
        country_code: Optional ISO 3166-1 alpha-2 country code.
        subdivision_code: Optional ISO 3166-2 subdivision code.

    Returns:
        ArenaAffiliation: The newly created affiliation (flushed, not committed).

    Raises:
        ValueError: If validation fails.
    """
    data = await validate_affiliation_data(
        session,
        name=name,
        url=url,
        country_code=country_code,
        subdivision_code=subdivision_code,
    )
    affiliation = ArenaAffiliation(
        id=str(uuid.uuid4()),
        name=data.name,
        url=data.url,
        country_code=data.country_code,
        subdivision_code=data.subdivision_code,
    )
    session.add(affiliation)
    await session.flush()
    return affiliation


async def update_affiliation(
    session: AsyncSession,
    affiliation: ArenaAffiliation,
    *,
    name: str,
    url: str | None,
    country_code: str | None,
    subdivision_code: str | None,
    exclude_from_ranking: bool = False,
) -> ArenaAffiliation:
    """Update and flush an Arena affiliation after validation.

    Args:
        session: Active async database session.
        affiliation: Existing affiliation to update.
        name: New display name.
        url: New optional public URL.
        country_code: New optional ISO 3166-1 alpha-2 country code.
        subdivision_code: New optional ISO 3166-2 subdivision code.
        exclude_from_ranking: When True, excludes the affiliation from ranking and rating.

    Returns:
        ArenaAffiliation: The updated affiliation (flushed, not committed).

    Raises:
        ValueError: If validation fails.
    """
    data = await validate_affiliation_data(
        session,
        name=name,
        url=url,
        country_code=country_code,
        subdivision_code=subdivision_code,
        exclude_id=affiliation.id,
    )
    affiliation.name = data.name
    affiliation.url = data.url
    affiliation.country_code = data.country_code
    affiliation.subdivision_code = data.subdivision_code
    affiliation.exclude_from_ranking = exclude_from_ranking
    await session.flush()
    return affiliation


async def set_logo(
    session: AsyncSession,
    affiliation: ArenaAffiliation,
    *,
    logo_base64: str,
    logo_mime: str,
    logo_thumbnail_base64: str | None = None,
) -> ArenaAffiliation:
    """Apply a new logo to an existing affiliation and flush.

    Args:
        session: Active async database session.
        affiliation: Affiliation to update.
        logo_base64: Base64-encoded logo image.
        logo_mime: MIME type of the logo image.
        logo_thumbnail_base64: Optional base64-encoded logo thumbnail (64x64).

    Returns:
        ArenaAffiliation: The updated affiliation (flushed, not committed).

    Raises:
        ValueError: If logo_base64 or logo_mime are empty.
    """
    affiliation.apply_logo(logo_base64=logo_base64, logo_mime=logo_mime, logo_thumbnail_base64=logo_thumbnail_base64)
    await session.flush()
    return affiliation


async def clear_logo(session: AsyncSession, affiliation: ArenaAffiliation) -> ArenaAffiliation:
    """Remove the logo from an existing affiliation and flush.

    Args:
        session: Active async database session.
        affiliation: Affiliation to update.

    Returns:
        ArenaAffiliation: The updated affiliation (flushed, not committed).
    """
    affiliation.clear_logo()
    await session.flush()
    return affiliation


async def delete_affiliation(session: AsyncSession, affiliation: ArenaAffiliation) -> None:
    """Detach linked users and delete an Arena affiliation.

    Explicitly nulls ``affiliation_id`` on all linked users before removal
    using a bulk UPDATE, avoiding ORM lazy-load of the ``users`` relationship
    during the subsequent ``session.delete`` call.  This is explicit and
    testable even though the FK already uses ``ON DELETE SET NULL``.

    The affiliation is then re-fetched with its ``users`` collection eagerly
    loaded so that SQLAlchemy can process the in-memory back-references
    without issuing a new SQL query outside an async context.

    Args:
        session: Active async database session.
        affiliation: Affiliation to delete.
    """
    # Bulk-update linked users so the DB FK is satisfied before delete.
    await session.execute(
        update(ArenaUser).where(ArenaUser.affiliation_id == affiliation.id).values(affiliation_id=None)
    )
    await session.flush()

    # Re-load the affiliation with its (now empty) users collection so the
    # ORM back-reference is populated and no lazy SQL fires during delete.
    refreshed = await session.scalar(
        select(ArenaAffiliation)
        .where(ArenaAffiliation.id == affiliation.id)
        .options(selectinload(ArenaAffiliation.users))
    )
    if refreshed is not None:
        await session.delete(refreshed)
    else:
        await session.delete(affiliation)
    await session.flush()
