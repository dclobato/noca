#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Read access to precomputed per-user statistics.

The statistics themselves are computed periodically by the rating worker
(``shared.services.arena_stats``) and stored as a JSON snapshot in
``arena_user_statistics``. This service only reads the latest snapshot for
the Arena public profile page; it performs no aggregation.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena import arena_user_statistics as _arena_user_statistics


async def get_user_statistics(session: AsyncSession, user_id: str) -> dict[str, Any] | None:
    """Return the latest statistics snapshot for a user, or ``None``.

    Args:
        session: Active async database session.
        user_id: UUID of the Arena user.

    Returns:
        dict | None: The precomputed payload augmented with ``computed_at``
        (ISO-8601 string), or ``None`` if statistics have not been computed yet.
    """
    row = (
        await session.execute(
            select(
                _arena_user_statistics.c.data,
                _arena_user_statistics.c.computed_at,
            ).where(_arena_user_statistics.c.user_id == user_id)
        )
    ).one_or_none()
    if row is None:
        return None
    payload: dict[str, Any] = dict(row.data)
    payload["computed_at"] = row.computed_at.isoformat()
    return payload
