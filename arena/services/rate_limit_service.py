#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Per-user submission rate limiting via a PostgreSQL sliding-window count.

Strategy
--------
Before each submission the service:

1. Acquires a transaction-scoped PostgreSQL advisory lock keyed on the user UUID
   (``pg_advisory_xact_lock``).  This serialises concurrent attempts from the
   same user (multiple browser tabs, scripted bots) within the same DB
   transaction.  On non-PostgreSQL dialects the lock step is skipped so SQLite
   test fixtures work without modification.

2. Counts submissions in the rolling window using a Python-computed cutoff
   timestamp.  Using ``datetime.now(UTC) - timedelta(...)`` instead of
   ``NOW() - INTERVAL '...'`` keeps the query portable across dialects and
   makes the cutoff easy to override in tests.

3. Returns ``(allowed, next_allowed_at)``.  ``next_allowed_at`` is the instant
   when the oldest in-window submission will fall outside the window, giving
   the caller a concrete "try again after" timestamp to show the user.

The advisory lock is intentionally isolated in ``acquire_submission_rate_lock``
so tests can monkeypatch it without touching the count logic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena.arena_submissions import arena_submissions


async def acquire_submission_rate_lock(session: AsyncSession, user_id: str) -> None:
    """Acquire a transaction-scoped advisory lock for *user_id* (PostgreSQL only).

    The lock is automatically released when the surrounding transaction commits
    or rolls back.  On non-PostgreSQL dialects this is a no-op so that SQLite
    test fixtures work without any patching.

    Args:
        session: Active async database session.
        user_id: Arena user UUID string used as the lock key.
    """
    conn = await session.connection()
    if conn.dialect.name != "postgresql":
        return
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:uid))"),
        {"uid": user_id},
    )


async def check_submission_rate_limit(
    session: AsyncSession,
    user_id: str,
    window_minutes: int,
    max_submissions: int,
) -> tuple[bool, datetime | None]:
    """Check whether *user_id* may submit within the current sliding window.

    Acquires the advisory lock first, then counts submissions in the rolling
    window.  Must be called inside the same transaction that will INSERT the
    new submission row so the lock is held across the check and the write.

    Args:
        session: Active async database session (caller owns the transaction).
        user_id: Arena user UUID.
        window_minutes: Length of the rolling window in minutes.
        max_submissions: Maximum submissions allowed within the window.

    Returns:
        ``(True, None)`` when the user is within the limit.
        ``(False, next_allowed_at)`` when the limit is reached, where
        ``next_allowed_at`` is the earliest moment the window will have a free
        slot (oldest in-window submission + window duration).
    """
    await acquire_submission_rate_lock(session, user_id)

    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=window_minutes)

    count = await session.scalar(
        select(func.count()).where(
            arena_submissions.c.user_id == user_id,
            arena_submissions.c.created_at > cutoff,
        )
    )

    if int(count or 0) < max_submissions:
        return True, None

    oldest_in_window = await session.scalar(
        select(func.min(arena_submissions.c.created_at)).where(
            arena_submissions.c.user_id == user_id,
            arena_submissions.c.created_at > cutoff,
        )
    )
    next_allowed_at = (oldest_in_window or now) + timedelta(minutes=window_minutes)
    return False, next_allowed_at
