#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Precomputed submission heatmap for Arena user profiles.

The rating worker calls :func:`compute_all_user_heatmaps` after each user
rating cycle to rebuild the 52-week calendar data for every user that has
submitted within the window.  The function does **not** commit; commit
ownership belongs to the caller.

Algorithm:
  1. Compute a 364-day UTC window (range_start … range_end inclusive).
  2. DELETE the entire ``arena_user_submission_heatmap`` table so stale rows
     for inactive users never survive a cycle.
  3. SELECT (user_id, created_at) for all submissions within the window in
     one query — no per-user loop, no DB-specific date functions.
  4. Aggregate by (user_id, UTC date) in Python.
  5. Bulk-INSERT one row per user (table is empty, so no conflict handling
     is needed).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena.arena_heatmap import arena_user_submission_heatmap
from shared.db_schema.arena.arena_submissions import arena_submissions


async def compute_all_user_heatmaps(session: AsyncSession) -> int:
    """Rebuild submission heatmaps for all users active in the last 364 days.

    Deletes every existing snapshot first so rows for inactive users are not
    carried forward.  Does **not** commit — the caller owns the transaction.

    Args:
        session: Active async database session.

    Returns:
        int: Number of user heatmaps written.
    """
    range_end = datetime.now(UTC).date()
    range_start = range_end - timedelta(days=363)  # 364 days inclusive

    window_start = datetime(range_start.year, range_start.month, range_start.day, tzinfo=UTC)
    window_end = datetime(range_end.year, range_end.month, range_end.day, tzinfo=UTC) + timedelta(days=1)

    await session.execute(delete(arena_user_submission_heatmap))

    result = await session.execute(
        select(arena_submissions.c.user_id, arena_submissions.c.created_at).where(
            arena_submissions.c.created_at >= window_start,
            arena_submissions.c.created_at < window_end,
        )
    )
    rows = result.fetchall()

    counts: dict[str, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    for user_id, ts in rows:
        # SQLite returns naive datetimes (implicitly UTC); PostgreSQL returns aware UTC.
        aware_ts = ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
        day = aware_ts.astimezone(UTC).date()
        counts[user_id][day] += 1

    if counts:
        await session.execute(
            insert(arena_user_submission_heatmap),
            [
                {
                    "user_id": uid,
                    "data": [[d.isoformat(), n] for d, n in sorted(days.items())],
                    "range_start": range_start.isoformat(),
                    "range_end": range_end.isoformat(),
                    "computed_at": datetime.now(UTC),
                }
                for uid, days in counts.items()
            ],
        )

    return len(counts)
