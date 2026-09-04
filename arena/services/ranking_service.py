#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public ranking queries for the Arena Ranking section."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_affiliations import ArenaAffiliation
from arena.services.identity_search_service import (
    prepare_affiliation_search,
    prepare_public_user_search,
)
from arena.services.leaderboard_service import build_ranked_affiliations_cte, build_ranked_users_cte
from arena.services.pagination_service import Pagination, PaginationParams, build_pagination_params
from arena.services.profile_location_service import LocationChoice, country_name, subdivision_name
from arena.services.user_visibility_service import resolve_display_identity

_PER_PAGE = 50


@dataclass(frozen=True)
class RankedUser:
    """Presentation-safe user ranking row.

    Attributes:
        id: Arena user UUID.
        rank: Global competition rank (ties share the same rank).
        name: **Resolved** public display name -- the username unless the user
            is an adult who opted in to their legal name. Never ``nome``.
        is_pseudonymous: True when ``name`` is the pseudonymous username.
        email_mascarado: Masked email address for display, or None when the
            user is age-shielded. A masked address beside an affiliation and a
            country re-identifies a minor, so the shield withholds it entirely
            and the template guards on this being set.
        affiliation_id: Affiliation UUID, or None.
        affiliation_name: Affiliation display name, or None.
        affiliation_has_logo: True when the affiliation has an uploaded logo.
        avatar_revision: Cache-busting revision of the user's effective avatar,
            appended as ``?v=`` to the avatar URL so a list page hits the browser cache.
        country_code: ISO 3166-1 alpha-2 country code, or None.
        country_name: Country display name, or None.
        subdivision_code: ISO 3166-2 subdivision code, or None.
        subdivision_name: Subdivision display name, or None.
        rating: Computed Arena user rating.
        solved: Number of distinct problems the user has solved.
        public_profile: **Effective** public-profile flag, after the age
            shield. A public profile page can be linked whenever this is True.

    Build rows with :meth:`from_row` and nothing else. Constructing this
    dataclass directly is how ``name=row.nome`` reappears and puts a minor's
    legal name back on the ranking pages.
    """

    id: str
    rank: int
    name: str
    is_pseudonymous: bool
    email_mascarado: str | None
    affiliation_id: str | None
    affiliation_name: str | None
    affiliation_has_logo: bool
    country_code: str | None
    country_name: str | None
    subdivision_code: str | None
    subdivision_name: str | None
    rating: int
    solved: int
    public_profile: bool
    avatar_revision: int

    @classmethod
    def from_row(cls, row: Any) -> RankedUser:
        """Build one ranking row from a CTE row, applying the age shield.

        Args:
            row: A result row from ``build_ranked_users_cte()`` joined with the
                affiliation, carrying the identity columns the shield needs.

        Returns:
            RankedUser: The presentation-safe row.
        """
        identity = resolve_display_identity(
            user_id=row.id,
            full_name=row.nome,
            username=row.username,
            date_of_birth=row.dta_nascimento,
            full_name_public=row.full_name_public,
            public_profile=row.public_profile,
            ranking_visible=row.ranking_visible,
            email=row.email_normalizado,
        )
        return cls(
            id=identity.user_id,
            rank=row.global_rank,
            name=identity.display_name,
            is_pseudonymous=identity.is_pseudonymous,
            email_mascarado=identity.masked_email,
            affiliation_id=row.affiliation_id,
            affiliation_name=row.affiliation_name,
            affiliation_has_logo=bool(row.affiliation_logo_base64),
            country_code=row.country_code,
            country_name=country_name(row.country_code),
            subdivision_code=row.subdivision_code,
            subdivision_name=subdivision_name(row.subdivision_code),
            rating=row.rating,
            solved=row.solved,
            public_profile=identity.public_profile,
            avatar_revision=row.avatar_revision,
        )


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
        subdivision_code: ISO 3166-2 subdivision code, or None.
        subdivision_name: Subdivision display name, or None.
        rating: Computed affiliation rating.
        solved: Total counted solves by ranking-visible affiliation members.
    """

    id: str
    rank: int
    name: str
    has_logo: bool
    country_code: str | None
    country_name: str | None
    subdivision_code: str | None
    subdivision_name: str | None
    rating: int
    solved: int


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
        search: Optional search text matched through the **public** indexed
            candidate query in ``identity_search_service``: full-text,
            substring, and fuzzy on the name for users who are not
            age-shielded, substring and fuzzy on the username for everyone, and
            substring on the email. Matching a shielded user's real name is
            suppressed because answering "is this name in the ranking?" while
            rendering a pseudonym would reconstruct the shield's own secret.
            Search only filters — it never reorders.
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
        base = base.where(ranked_cte.c.id.in_(await prepare_public_user_search(session, search)))

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await session.execute(base.offset(params.offset).limit(params.per_page))).all()

    items = [RankedUser.from_row(row) for row in rows]
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
        search: Optional search text matched against the affiliation name
            through the indexed candidate query in ``identity_search_service``:
            full-text, substring, and fuzzy. Search only filters — it never
            reorders.
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
        base = base.where(ranked_cte.c.id.in_(await prepare_affiliation_search(session, search)))

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
            subdivision_code=row.subdivision_code,
            subdivision_name=subdivision_name(row.subdivision_code),
            rating=row.rating,
            solved=row.solved,
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
