#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Web submission rate limiting service.

This service enforces per-team submission rate limits using a sliding window
counter pattern. Rate limits are enforced by counting the number of submissions
from a team within a specified time window (in seconds) and checking if they
exceed the maximum allowed submissions.

The service uses PostgreSQL advisory locks to ensure atomicity of the check and
increment operations. On non-PostgreSQL databases, the service is a no-op.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.submission import submissions


async def acquire_submission_rate_lock(session: AsyncSession, team_id: str) -> None:
    """Acquire an exclusive advisory lock for a team's rate limit check.

    This function uses PostgreSQL's advisory locking mechanism to ensure atomic
    rate limit checks across concurrent requests. On non-PostgreSQL databases,
    this is a no-op.

    Args:
        session: The async database session.
        team_id: The team ID to lock on.
    """
    conn = await session.connection()
    if conn.dialect.name != "postgresql":
        return
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:uid))"),
        {"uid": team_id},
    )


async def check_submission_rate_limit(
    session: AsyncSession,
    team_id: str,
    window_seconds: int,
    max_submissions: int,
) -> tuple[bool, datetime | None]:
    """Check if a team is within their submission rate limit.

    This function checks whether a team has exceeded their maximum number of
    submissions within the specified time window. The time window is measured
    in seconds from the current time.

    Args:
        session: The async database session.
        team_id: The team ID to check.
        window_seconds: The time window in seconds.
        max_submissions: The maximum number of submissions allowed in the window.

    Returns:
        A tuple of (allowed, next_allowed_at) where:
        - allowed: True if the team is within their rate limit, False otherwise.
        - next_allowed_at: If allowed is True, this is None. If allowed is False,
          this is the earliest datetime when the team can submit again.
    """
    await acquire_submission_rate_lock(session, team_id)
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=window_seconds)
    count = await session.scalar(
        select(func.count()).where(
            submissions.c.team_id == team_id,
            submissions.c.created_at > cutoff,
        )
    )
    if int(count or 0) < max_submissions:
        return True, None
    oldest_in_window = await session.scalar(
        select(func.min(submissions.c.created_at)).where(
            submissions.c.team_id == team_id,
            submissions.c.created_at > cutoff,
        )
    )
    next_allowed_at = (oldest_in_window or now) + timedelta(seconds=window_seconds)
    return False, next_allowed_at
