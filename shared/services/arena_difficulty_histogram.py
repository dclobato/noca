#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Catalogue-wide problem-difficulty histogram snapshot.

Called by :func:`shared.services.arena_rating.rate_all_problems` at the end of
every difficulty-recompute cycle to bucket the freshly computed internal
difficulties (``[1, 100]``) into 20 bins over the ``[0, 10]`` display scale and
persist the snapshot into the singleton ``arena_rating_cycle_state`` row. The
Arena help page reads this snapshot to show a current distribution chart.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena.arena_rating_cycle_state import (
    RATING_CYCLE_STATE_ID,
    arena_rating_cycle_state,
)

#: Number of histogram bins over the [0, 10] display-difficulty scale.
BIN_COUNT = 20

#: Width of each bin on the display scale.
BIN_WIDTH = 10.0 / BIN_COUNT


def build_difficulty_histogram(difficulties: list[int]) -> dict[str, Any]:
    """Bucket internal difficulties into a 20-bin histogram over [0, 10].

    Args:
        difficulties: Internal difficulty values in [1, 100], one per problem.

    Returns:
        dict: JSON-serializable histogram payload with per-bin counts.
    """
    counts = [0] * BIN_COUNT
    for difficulty in difficulties:
        display = difficulty / 10.0
        bin_index = min(BIN_COUNT - 1, int(display / BIN_WIDTH))
        counts[bin_index] += 1
    return {
        "bins": BIN_COUNT,
        "bin_width": BIN_WIDTH,
        "scale_min": 0.0,
        "scale_max": 10.0,
        "counts": counts,
        "total_problems": len(difficulties),
    }


async def persist_difficulty_histogram(session: AsyncSession, difficulties: list[int], computed_at: datetime) -> None:
    """Build and upsert the difficulty histogram into the singleton snapshot row.

    Does **not** commit; caller is responsible for the transaction.

    Args:
        session: Active async database session.
        difficulties: Internal difficulty values in [1, 100], one per problem.
        computed_at: Timestamp of the difficulty cycle that produced this snapshot.
    """
    payload = build_difficulty_histogram(difficulties)
    insert = sqlite_insert if session.bind is not None and session.bind.dialect.name == "sqlite" else pg_insert
    stmt = insert(arena_rating_cycle_state).values(id=RATING_CYCLE_STATE_ID, data=payload, computed_at=computed_at)
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"],
        set_={"data": payload, "computed_at": computed_at},
    )
    await session.execute(stmt)
