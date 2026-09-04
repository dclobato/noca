#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reusable leaderboard queries for Arena users."""

from __future__ import annotations

from dataclasses import dataclass
from math import exp
from typing import Any

from sqlalchemy import ColumnElement, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import CTE

from arena.models.arena_affiliations import ArenaAffiliation
from arena.models.arena_users import ArenaUser
from arena.services.user_visibility_service import resolve_display_identity
from shared.services.arena_rating import CONFIDENCE_SCALE


@dataclass(frozen=True)
class TopRatedUser:
    """Presentation-safe Arena user ranking row.

    Attributes:
        id: Arena user UUID.
        rank: Competition rank based only on rating.
        name: **Resolved** public display name -- the username unless the user
            is an adult who opted in to their legal name. Never ``nome``.
        is_pseudonymous: True when ``name`` is the pseudonymous username.
        rating: Computed Arena user rating.
        confidence: Confidence percentage for the computed rating.
        solved_problems: Number of solved problems reflected in the rating.
        public_profile: **Effective** public-profile flag, after the age
            shield. A profile link is safe whenever this is True.
        avatar_revision: Cache-busting revision of the user's effective avatar,
            appended as ``?v=`` to the avatar URL so a list page hits the browser cache.

    Build rows with :meth:`from_row` and nothing else. Constructing this
    dataclass directly is how ``name=row.nome`` reappears and puts a minor's
    legal name back on the anonymous dashboard.
    """

    id: str
    rank: int
    name: str
    is_pseudonymous: bool
    rating: int
    confidence: int
    solved_problems: int
    public_profile: bool
    avatar_revision: int

    @classmethod
    def from_row(cls, row: Any, *, rank: int) -> TopRatedUser:
        """Build one ranking row from a query row, applying the age shield.

        Args:
            row: A result row carrying ``id``, ``nome``, ``username``,
                ``dta_nascimento``, ``full_name_public``, ``ranking_visible``,
                ``rating``, ``solved_problems``, and ``public_profile``.
            rank: The competition rank already computed for this row.

        Returns:
            TopRatedUser: The presentation-safe row.
        """
        identity = resolve_display_identity(
            user_id=row.id,
            full_name=row.nome,
            username=row.username,
            date_of_birth=row.dta_nascimento,
            full_name_public=row.full_name_public,
            public_profile=row.public_profile,
            ranking_visible=row.ranking_visible,
        )
        return cls(
            id=identity.user_id,
            rank=rank,
            name=identity.display_name,
            is_pseudonymous=identity.is_pseudonymous,
            rating=row.rating,
            confidence=_rating_confidence(row.solved_problems),
            solved_problems=row.solved_problems,
            public_profile=identity.public_profile,
            avatar_revision=row.avatar_revision,
        )


def _eligible_users_where() -> list[ColumnElement[bool]]:
    """Return base WHERE conditions for eligible ranked Arena users."""
    return [
        ArenaUser.ativo.is_(True),
        ArenaUser.email_confirmado.is_(True),
        ArenaUser.ranking_visible.is_(True),
    ]


def build_ranked_users_cte() -> CTE:
    """Build a CTE that assigns a global RANK to every eligible Arena user.

    The rank is based solely on ``user_rating`` so tied ratings share the same
    rank number.  Tie-breaker ordering (solved count, creation date, id) belongs
    in the outer query's ``ORDER BY``.

    Returns:
        CTE: SQLAlchemy CTE named ``ranked_users`` with columns
        ``id``, ``nome``, ``username``, ``dta_nascimento``,
        ``full_name_public``, ``ranking_visible``, ``email_normalizado``,
        ``affiliation_id``, ``country_code``, ``subdivision_code``,
        ``created_at``, ``rating``, ``solved``, ``public_profile``, and
        ``global_rank``. The four identity columns feed the age shield in
        ``RankedUser.from_row()``; ``ranking_visible`` is selected rather than
        assumed from the eligibility filter, so the resolver reads the stored
        value and cannot diverge if that filter is ever relaxed.
    """
    return (
        select(
            ArenaUser.id,
            ArenaUser.nome,
            ArenaUser.username,
            ArenaUser.dta_nascimento,
            ArenaUser.full_name_public,
            ArenaUser.ranking_visible,
            ArenaUser.email_normalizado,
            ArenaUser.affiliation_id,
            ArenaUser.country_code,
            ArenaUser.subdivision_code,
            ArenaUser.created_at,
            func.coalesce(ArenaUser.user_rating, 0).label("rating"),
            func.coalesce(ArenaUser.solved_problems, 0).label("solved"),
            ArenaUser.public_profile,
            ArenaUser.avatar_revision,
            func.rank().over(order_by=[func.coalesce(ArenaUser.user_rating, 0).desc()]).label("global_rank"),
        )
        .where(*_eligible_users_where())
        .cte("ranked_users")
    )


def build_ranked_affiliations_cte() -> CTE:
    """Build a CTE that assigns a global RANK to every Arena affiliation.

    The rank is based solely on ``rating`` so tied ratings share the same
    rank number.  Tie-breaker ordering (name, id) belongs in the outer
    query's ``ORDER BY``.

    Returns:
        CTE: SQLAlchemy CTE named ``ranked_affiliations`` with columns
        ``id``, ``name``, ``country_code``, ``subdivision_code``,
        ``has_logo``, ``rating``, ``solved``, and ``global_rank``.
    """
    return (
        select(
            ArenaAffiliation.id,
            ArenaAffiliation.name,
            ArenaAffiliation.country_code,
            ArenaAffiliation.subdivision_code,
            (ArenaAffiliation.logo_base64.isnot(None)).label("has_logo"),
            func.coalesce(ArenaAffiliation.rating, 0).label("rating"),
            func.coalesce(ArenaAffiliation.solved_problems, 0).label("solved"),
            func.rank().over(order_by=[func.coalesce(ArenaAffiliation.rating, 0).desc()]).label("global_rank"),
        )
        .where(ArenaAffiliation.exclude_from_ranking.is_(False))
        .cte("ranked_affiliations")
    )


def _rating_confidence(solved_problems: int) -> int:
    """Return the rating confidence percentage for a solved-problem count.

    Args:
        solved_problems: Number of solved problems reflected in the rating.

    Returns:
        Confidence percentage using the same formula as ``ArenaUser``.
    """
    if solved_problems <= 0:
        return 0
    return round(100 * (1 - exp(-solved_problems / CONFIDENCE_SCALE)))


async def get_top_rated_users(session: AsyncSession, *, limit: int) -> list[TopRatedUser]:
    """Return the top Arena participants ordered by rating.

    Args:
        session: Active async database session.
        limit: Maximum number of users to return. Values below 1 return an empty list.

    Returns:
        Ranked Arena participants, excluding inactive, unconfirmed, and hidden accounts.
        Equal ratings share the same rank; confidence and creation date only
        order users inside a tied rating group. Names are resolved through the
        age shield, so an age-shielded participant appears under their username
        -- this list is rendered to **anonymous** visitors on ``/dashboard``.
    """
    if limit < 1:
        return []

    conditions: list[ColumnElement[bool]] = _eligible_users_where()

    rows = (
        await session.execute(
            select(
                ArenaUser.id,
                ArenaUser.nome,
                ArenaUser.username,
                ArenaUser.dta_nascimento,
                ArenaUser.full_name_public,
                ArenaUser.ranking_visible,
                func.coalesce(ArenaUser.user_rating, 0).label("rating"),
                func.coalesce(ArenaUser.solved_problems, 0).label("solved_problems"),
                ArenaUser.created_at,
                ArenaUser.public_profile,
                ArenaUser.avatar_revision,
            )
            .where(*conditions)
            .order_by(
                desc("rating"),
                desc("solved_problems"),
                ArenaUser.created_at.asc(),
                ArenaUser.id.asc(),
            )
            .limit(limit)
        )
    ).all()
    top_users: list[TopRatedUser] = []
    previous_rating: int | None = None
    current_rank = 0
    for position, row in enumerate(rows, start=1):
        if previous_rating is None or row.rating != previous_rating:
            current_rank = position
            previous_rating = row.rating
        top_users.append(TopRatedUser.from_row(row, rank=current_rank))

    return top_users
