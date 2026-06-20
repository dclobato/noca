#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Read access to precomputed per-problem statistics.

The statistics themselves are computed periodically by the rating worker
(``shared.services.arena_stats``) and stored as a JSON snapshot in
``arena_problem_statistics``. This service only reads the latest snapshot for
the Arena statistics page; it performs no aggregation.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena import arena_problem_statistics as _arena_problem_statistics


async def get_problem_statistics(session: AsyncSession, problem_id: str) -> dict[str, Any] | None:
    """Return the latest statistics snapshot for a problem, or ``None``.

    Args:
        session: Active async database session.
        problem_id: UUID of the Arena problem.

    Returns:
        dict | None: The precomputed payload augmented with ``computed_at``
        (ISO-8601 string), or ``None`` if statistics have not been computed yet.
    """
    row = (
        await session.execute(
            select(
                _arena_problem_statistics.c.data,
                _arena_problem_statistics.c.computed_at,
            ).where(_arena_problem_statistics.c.problem_id == problem_id)
        )
    ).one_or_none()
    if row is None:
        return None
    payload: dict[str, Any] = dict(row.data)
    payload["computed_at"] = row.computed_at.isoformat()
    return payload
