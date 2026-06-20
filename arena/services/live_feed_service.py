#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Build the public Arena live submission feed snapshot.

The snapshot owns the last-20 query; the SSE channel only signals "something
changed" so the browser refetches this snapshot. Unlike the per-user submission
history (``submission_list_service``), this lists finalized submissions across
all users.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.services.profile_location_service import country_name
from shared.db_schema import languages as languages_table
from shared.db_schema.arena import (
    arena_affiliations,
    arena_problems,
    arena_submission_judgments,
    arena_submissions,
    arena_users,
)
from shared.services.arena_query_helpers import active_arena_judgment_subquery


@dataclass(frozen=True)
class ArenaLiveFeedRow:
    """One row of the public Arena live feed.

    Attributes:
        submission_id: UUID of the submission (used by the client to diff rows).
        created_at: Wall-clock submission time (serialized as ISO 8601 UTC).
        affiliation_id: UUID of the user's affiliation, or None.
        affiliation_name: Display name of the user's affiliation, or None.
        affiliation_has_logo: Whether the affiliation has a stored logo.
        country_code: User location ISO 3166-1 alpha-2 country code, or None.
        country_name: User location country display name, or None.
        problem_number: Public sequential problem number (used to build the link).
        problem_title: Problem display title.
        language_name: Language display name.
        language_icon: Devicon CSS class string.
        verdict: Final verdict value.
    """

    submission_id: str
    created_at: datetime
    affiliation_id: str | None
    affiliation_name: str | None
    affiliation_has_logo: bool
    country_code: str | None
    country_name: str | None
    problem_number: int
    problem_title: str
    language_name: str
    language_icon: str
    verdict: str


@dataclass(frozen=True)
class ArenaLiveFeedSnapshot:
    """Public Arena live feed rows plus pagination metadata.

    Attributes:
        rows: Finalized submissions shown in the public live feed.
        limit: Configured maximum number of rows returned to the browser.
        has_more: True when older finalized submissions exist beyond ``rows``.
    """

    rows: list[ArenaLiveFeedRow]
    limit: int
    has_more: bool


async def build_arena_live_feed_snapshot(session: AsyncSession) -> ArenaLiveFeedSnapshot:
    """Return the newest finalized Arena submissions and overflow metadata.

    The limit is applied in SQL against the active judgment join with one extra
    row fetched to detect whether older submissions exist beyond the configured
    cap.

    Args:
        session: Active async database session.

    Returns:
        ArenaLiveFeedSnapshot: Rows and metadata for the public live feed.
    """
    active_j = active_arena_judgment_subquery()
    limit = settings.ARENA_LIVE_FEED_LIMIT

    stmt = (
        select(
            arena_submissions.c.id,
            arena_submissions.c.created_at,
            arena_affiliations.c.id.label("affiliation_id"),
            arena_affiliations.c.name.label("affiliation_name"),
            arena_affiliations.c.logo_base64.label("affiliation_logo_base64"),
            arena_affiliations.c.logo_mime.label("affiliation_logo_mime"),
            arena_users.c.country_code,
            arena_problems.c.arena_number,
            arena_problems.c.title,
            languages_table.c.name,
            languages_table.c.icon,
            arena_submission_judgments.c.final_verdict,
        )
        .select_from(
            arena_submissions.join(
                arena_problems,
                arena_submissions.c.problem_id == arena_problems.c.id,
            )
            .join(arena_users, arena_submissions.c.user_id == arena_users.c.id)
            .outerjoin(arena_affiliations, arena_users.c.affiliation_id == arena_affiliations.c.id)
            .join(languages_table, arena_submissions.c.language_id == languages_table.c.id)
            .join(active_j, active_j.c.submission_id == arena_submissions.c.id)
            .join(
                arena_submission_judgments,
                and_(
                    arena_submission_judgments.c.submission_id == arena_submissions.c.id,
                    arena_submission_judgments.c.created_at == active_j.c.max_created_at,
                ),
            )
        )
        .where(arena_submission_judgments.c.final_verdict.isnot(None))
        .order_by(arena_submissions.c.created_at.desc())
        .limit(limit + 1)
    )

    rows = (await session.execute(stmt)).all()
    visible_rows = rows[:limit]
    feed_rows = [
        ArenaLiveFeedRow(
            submission_id=row[0],
            created_at=row[1],
            affiliation_id=row[2],
            affiliation_name=row[3],
            affiliation_has_logo=bool(row[4] and row[5]),
            country_code=row[6],
            country_name=country_name(row[6]),
            problem_number=row[7],
            problem_title=row[8],
            language_name=row[9],
            language_icon=row[10],
            verdict=str(row[11]),
        )
        for row in visible_rows
    ]
    return ArenaLiveFeedSnapshot(rows=feed_rows, limit=limit, has_more=len(rows) > limit)
