#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public ranking queries for the Arena Ranking section."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_affiliations import ArenaAffiliation
from arena.services.leaderboard_service import build_ranked_affiliations_cte, build_ranked_users_cte
from arena.services.pagination_service import Pagination, PaginationParams, build_pagination_params
from arena.services.profile_location_service import LocationChoice, country_name, subdivision_name
from shared.services.email_validation import EmailValidationService

_PER_PAGE = 50


@dataclass(frozen=True)
class RankedUser:
    """Presentation-safe user ranking row.

    Attributes:
        id: Arena user UUID.
        rank: Global competition rank (ties share the same rank).
        name: Public display name.
        email_mascarado: Masked email address for display.
        affiliation_id: Affiliation UUID, or None.
        affiliation_name: Affiliation display name, or None.
        affiliation_has_logo: True when the affiliation has an uploaded logo.
        country_code: ISO 3166-1 alpha-2 country code, or None.
        country_name: Country display name, or None.
        subdivision_name: Subdivision display name, or None.
        rating: Computed Arena user rating.
    """

    id: str
    rank: int
    name: str
    email_mascarado: str
    affiliation_id: str | None
    affiliation_name: str | None
    affiliation_has_logo: bool
    country_code: str | None
    country_name: str | None
    subdivision_name: str | None
    rating: int


@dataclass(frozen=True)
class RankedAffiliation:
    """Presentation-safe affiliation ranking row.

    Attributes:
        id: Arena affiliation UUID.
        rank: Global competition rank (ties share the same rank).
        name: Affiliation display name.
        has_logo: True when the affiliation has an uploaded logo.
        country_code: ISO 3166-1 alpha-2 country code, or None.
        country_name: Country display name, or None.
        subdivision_name: Subdivision display name, or None.
        rating: Computed affiliation rating.
    """

    id: str
    rank: int
    name: str
    has_logo: bool
    country_code: str | None
    country_name: str | None
    subdivision_name: str | None
    rating: int


async def get_ranked_users_paginated(
    session: AsyncSession,
    *,
    search: str | None = None,
    affiliation_id: str | None = None,
    page: int = 1,
    per_page: int = _PER_PAGE,
) -> Pagination[RankedUser]:
    """Return a paginated user ranking with global rank positions.

    Rank is computed over all eligible users before any search or affiliation
    filter is applied, so rank #47 remains #47 in filtered results.

    Args:
        session: Active async database session.
        search: Optional case-insensitive substring for name or email.
        affiliation_id: Optional affiliation UUID to scope the results.
        page: Requested page number (1-based).
        per_page: Items per page.

    Returns:
        Pagination[RankedUser]: Paginated ranked user list.
    """
    params = build_pagination_params(page, per_page=per_page)
    ranked_cte = build_ranked_users_cte()

    base = (
        select(
            ranked_cte,
            ArenaAffiliation.name.label("affiliation_name"),
            ArenaAffiliation.logo_base64.label("affiliation_logo_base64"),
        )
        .outerjoin(ArenaAffiliation, ranked_cte.c.affiliation_id == ArenaAffiliation.id)
        .order_by(
            ranked_cte.c.global_rank.asc(),
            ranked_cte.c.solved.desc(),
            ranked_cte.c.created_at.asc(),
            ranked_cte.c.id.asc(),
        )
    )

    if affiliation_id:
        base = base.where(ranked_cte.c.affiliation_id == affiliation_id)

    if search and search.strip():
        like = f"%{search.strip().lower()}%"
        base = base.where(
            or_(
                func.lower(ranked_cte.c.nome).like(like),
                func.lower(ranked_cte.c.email_normalizado).like(like),
            )
        )

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await session.execute(base.offset(params.offset).limit(params.per_page))).all()

    items = [
        RankedUser(
            id=row.id,
            rank=row.global_rank,
            name=row.nome,
            email_mascarado=EmailValidationService.mask(row.email_normalizado),
            affiliation_id=row.affiliation_id,
            affiliation_name=row.affiliation_name,
            affiliation_has_logo=bool(row.affiliation_logo_base64),
            country_code=row.country_code,
            country_name=country_name(row.country_code),
            subdivision_name=subdivision_name(row.subdivision_code),
            rating=row.rating,
        )
        for row in rows
    ]
    return Pagination(items=items, page=params.page, per_page=params.per_page, total=total)


async def get_ranked_affiliations_paginated(
    session: AsyncSession,
    *,
    search: str | None = None,
    country_code: str | None = None,
    subdivision_code: str | None = None,
    page: int = 1,
    per_page: int = _PER_PAGE,
) -> Pagination[RankedAffiliation]:
    """Return a paginated affiliation ranking with global rank positions.

    Args:
        session: Active async database session.
        search: Optional case-insensitive substring for affiliation name.
        country_code: Optional ISO country code filter.
        subdivision_code: Optional ISO subdivision code filter (requires country_code).
        page: Requested page number (1-based).
        per_page: Items per page.

    Returns:
        Pagination[RankedAffiliation]: Paginated ranked affiliation list.
    """
    params: PaginationParams = build_pagination_params(page, per_page=per_page)
    ranked_cte = build_ranked_affiliations_cte()

    base = select(ranked_cte).order_by(
        ranked_cte.c.global_rank.asc(),
        func.lower(ranked_cte.c.name).asc(),
        ranked_cte.c.id.asc(),
    )

    if search and search.strip():
        base = base.where(func.lower(ranked_cte.c.name).like(f"%{search.strip().lower()}%"))

    if country_code:
        base = base.where(ranked_cte.c.country_code == country_code)
        if subdivision_code:
            base = base.where(ranked_cte.c.subdivision_code == subdivision_code)

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await session.execute(base.offset(params.offset).limit(params.per_page))).all()

    items = [
        RankedAffiliation(
            id=row.id,
            rank=row.global_rank,
            name=row.name,
            has_logo=bool(row.has_logo),
            country_code=row.country_code,
            country_name=country_name(row.country_code),
            subdivision_name=subdivision_name(row.subdivision_code),
            rating=row.rating,
        )
        for row in rows
    ]
    return Pagination(items=items, page=params.page, per_page=params.per_page, total=total)


async def get_affiliation_filter_options(
    session: AsyncSession,
    *,
    country_code: str | None = None,
) -> tuple[list[LocationChoice], list[LocationChoice]]:
    """Return country and subdivision choices sourced from actual affiliation data.

    Only countries/subdivisions that appear in the affiliations table are
    included — the full pycountry list is not used, keeping the filter
    relevant to available data.

    Args:
        session: Active async database session.
        country_code: When provided, fetch subdivisions for this country.

    Returns:
        tuple: ``(countries, subdivisions)`` as lists of LocationChoice.
    """
    country_rows = (
        (
            await session.execute(
                select(distinct(ArenaAffiliation.country_code)).where(ArenaAffiliation.country_code.isnot(None))
            )
        )
        .scalars()
        .all()
    )

    countries = sorted(
        [
            LocationChoice(code=str(code), name=country_name(code) or str(code))
            for code in country_rows
            if code is not None
        ],
        key=lambda c: c.name.casefold(),
    )

    subdivisions: list[LocationChoice] = []
    if country_code:
        subdiv_rows = (
            (
                await session.execute(
                    select(distinct(ArenaAffiliation.subdivision_code)).where(
                        ArenaAffiliation.country_code == country_code,
                        ArenaAffiliation.subdivision_code.isnot(None),
                    )
                )
            )
            .scalars()
            .all()
        )

        subdivisions = sorted(
            [
                LocationChoice(code=str(code), name=subdivision_name(code) or str(code))
                for code in subdiv_rows
                if code is not None
            ],
            key=lambda s: s.name.casefold(),
        )

    return countries, subdivisions


async def get_affiliation_or_404(session: AsyncSession, affiliation_id: str) -> ArenaAffiliation:
    """Fetch an Arena affiliation by id, raising HTTP 404 if not found.

    Args:
        session: Active async database session.
        affiliation_id: UUID of the affiliation.

    Returns:
        ArenaAffiliation: The found affiliation record.

    Raises:
        HTTPException: 404 if the affiliation does not exist.
    """
    affiliation = await session.get(ArenaAffiliation, affiliation_id)
    if affiliation is None:
        raise HTTPException(status_code=404, detail="Affiliation not found")
    return affiliation
