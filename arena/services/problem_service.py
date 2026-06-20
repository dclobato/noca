#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena problem lookup helpers.

Arena problems keep UUID primary keys for relational storage and judging
payloads, while ``arena_number`` is the stable public reference users can see
in URLs or admin screens.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import ArenaProblem


async def get_problem_by_arena_number(
    session: AsyncSession,
    arena_number: int,
) -> ArenaProblem | None:
    """Return an Arena problem by its public sequential number.

    Args:
        session: Open database session.
        arena_number: Positive public Arena problem number.

    Returns:
        The matching problem, or ``None`` when no row exists.
    """
    if arena_number < 1:
        return None

    result = await session.execute(select(ArenaProblem).where(ArenaProblem.arena_number == arena_number))
    return result.scalar_one_or_none()
