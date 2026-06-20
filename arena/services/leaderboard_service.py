#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reusable leaderboard queries for Arena users."""

from __future__ import annotations

from dataclasses import dataclass
from math import exp

from sqlalchemy import ColumnElement, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import CTE

from arena.models.arena_affiliations import ArenaAffiliation
from arena.models.arena_users import ArenaUser
from shared.enumerations import ArenaRole
from shared.services.arena_rating import CONFIDENCE_SCALE


@dataclass(frozen=True)
class TopRatedUser:
    """Presentation-safe Arena user ranking row.

    Attributes:
        id: Arena user UUID.
        rank: Competition rank based only on rating.
        name: Public display name.
        rating: Computed Arena user rating.
        confidence: Confidence percentage for the computed rating.
        solved_problems: Number of solved problems reflected in the rating.
    """

    id: str
    rank: int
    name: str
    rating: int
    confidence: int
    solved_problems: int


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
        ``id``, ``nome``, ``email_normalizado``, ``affiliation_id``,
        ``country_code``, ``subdivision_code``, ``created_at``,
        ``rating``, ``solved``, and ``global_rank``.
    """
    return (
        select(
            ArenaUser.id,
            ArenaUser.nome,
            ArenaUser.email_normalizado,
            ArenaUser.affiliation_id,
            ArenaUser.country_code,
            ArenaUser.subdivision_code,
            ArenaUser.created_at,
            func.coalesce(ArenaUser.user_rating, 0).label("rating"),
            func.coalesce(ArenaUser.solved_problems, 0).label("solved"),
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
        ``has_logo``, ``rating``, and ``global_rank``.
    """
    return (
        select(
            ArenaAffiliation.id,
            ArenaAffiliation.name,
            ArenaAffiliation.country_code,
            ArenaAffiliation.subdivision_code,
            (ArenaAffiliation.logo_base64.isnot(None)).label("has_logo"),
            func.coalesce(ArenaAffiliation.rating, 0).label("rating"),
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


async def get_top_rated_users(session: AsyncSession, *, limit: int, only_users: bool = True) -> list[TopRatedUser]:
    """Return the top Arena participants ordered by rating.

    Args:
        session: Active async database session.
        limit: Maximum number of users to return. Values below 1 return an empty list.
        only_users: If True, include only ARENA_USERS

    Returns:
        Ranked Arena participants, excluding inactive, unconfirmed, and staff accounts.
        Equal ratings share the same rank; confidence and creation date only
        order users inside a tied rating group.
    """
    if limit < 1:
        return []

    conditions: list[ColumnElement[bool]] = _eligible_users_where()
    if only_users:
        conditions = [*conditions, ArenaUser.role == ArenaRole.ARENA_USER]

    rows = (
        await session.execute(
            select(
                ArenaUser.id,
                ArenaUser.nome,
                func.coalesce(ArenaUser.user_rating, 0).label("rating"),
                func.coalesce(ArenaUser.solved_problems, 0).label("solved_problems"),
                ArenaUser.created_at,
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
        top_users.append(
            TopRatedUser(
                id=row.id,
                rank=current_rank,
                name=row.nome,
                rating=row.rating,
                confidence=_rating_confidence(row.solved_problems),
                solved_problems=row.solved_problems,
            )
        )

    return top_users
