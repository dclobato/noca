#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest discovery for the Animator landing page."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from animator.models.query_records import ensure_utc
from shared.db_schema import contests


@dataclass(frozen=True)
class AnimatorContestSummary:
    """Presentation-safe summary of one discoverable Animator contest.

    Attributes:
        login_slug: Public slug used by the contest launcher.
        contest_name: Human-readable contest name.
        start_time: Contest start instant in UTC.
        end_time: Contest end instant in UTC.
    """

    login_slug: str
    contest_name: str
    start_time: datetime
    end_time: datetime

    @property
    def start_iso(self) -> str:
        """Return the contest start as an ISO 8601 value."""
        return self.start_time.isoformat()

    @property
    def end_iso(self) -> str:
        """Return the contest end as an ISO 8601 value."""
        return self.end_time.isoformat()

    @property
    def start_label(self) -> str:
        """Return the contest start as a concise UTC display label."""
        return self.start_time.strftime("%b %d, %Y · %H:%M UTC")

    @property
    def end_label(self) -> str:
        """Return the contest end as a concise UTC display label."""
        return self.end_time.strftime("%b %d, %Y · %H:%M UTC")


@dataclass(frozen=True)
class AnimatorContestGroups:
    """Animator contests grouped by their current lifecycle state."""

    live: tuple[AnimatorContestSummary, ...]
    upcoming: tuple[AnimatorContestSummary, ...]
    past: tuple[AnimatorContestSummary, ...]

    @property
    def total(self) -> int:
        """Return the number of discoverable contests across all groups."""
        return len(self.live) + len(self.upcoming) + len(self.past)


async def list_animator_contests(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> AnimatorContestGroups:
    """List enabled, non-archived contests grouped for the Animator index.

    Args:
        session: Active database session.
        now: Optional reference instant, primarily for deterministic tests.

    Returns:
        Eligible contests grouped as live, upcoming, and past.
    """
    reference = ensure_utc(now) if now is not None else datetime.now(UTC)
    result = await session.execute(
        select(
            contests.c.login_slug,
            contests.c.contest_name,
            contests.c.start_time,
            contests.c.duration_minutes,
        ).where(
            contests.c.animator_enabled.is_(True),
            contests.c.active.is_(True),
        )
    )

    live: list[AnimatorContestSummary] = []
    upcoming: list[AnimatorContestSummary] = []
    past: list[AnimatorContestSummary] = []
    for row in result:
        start_time = ensure_utc(row.start_time)
        summary = AnimatorContestSummary(
            login_slug=str(row.login_slug),
            contest_name=str(row.contest_name),
            start_time=start_time,
            end_time=start_time + timedelta(minutes=int(row.duration_minutes)),
        )
        if reference < summary.start_time:
            upcoming.append(summary)
        elif reference <= summary.end_time:
            live.append(summary)
        else:
            past.append(summary)

    return AnimatorContestGroups(
        live=tuple(sorted(live, key=lambda contest: (contest.end_time, contest.login_slug))),
        upcoming=tuple(sorted(upcoming, key=lambda contest: (contest.start_time, contest.login_slug))),
        past=tuple(
            sorted(
                past,
                key=lambda contest: (contest.end_time, contest.login_slug),
                reverse=True,
            )
        ),
    )
