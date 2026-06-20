#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Build the public live submission feed snapshot for a contest.

The snapshot is the single source of truth for the live feed table: it owns the
last-20 query and the scoreboard-blackout anonymization. The SSE channel only
signals "something changed"; the browser refetches this snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Subquery

from shared.db_schema import languages as languages_table
from shared.db_schema import problems as problems_table
from shared.db_schema import sites as sites_table
from shared.db_schema import submission_judgments as submission_judgments_table
from shared.db_schema import submissions as submissions_table
from shared.db_schema import users as users_table
from shared.enumerations import JudgmentStatus, RoleEnum
from web.models.contest import Contest
from web.routes.contest_admin_problem_helpers import _label
from web.services.assorted_utils import contest_verdict_badge_class, format_site_identity

_SUPERSEDED = JudgmentStatus.SUPERSEDED.value
CONTEST_LIVE_FEED_LIMIT = 20
_FROZEN_TEAM_PLACEHOLDER = "—"
_FROZEN_BADGE_CLASS = "bg-secondary"


@dataclass(frozen=True)
class LiveFeedRow:
    """One row of the public contest live feed.

    Attributes:
        submission_id: UUID of the submission (used by the client to diff rows).
        created_at: Wall-clock submission time (serialized as ISO 8601 UTC).
        team: Display label for the team, or a neutral placeholder when frozen.
        problem_label: Ordinal letter label (e.g. ``A``).
        problem_name: Problem title.
        language_name: Language display name.
        language_icon: Devicon CSS class string.
        verdict: Final verdict value, or ``None`` when masked by the freeze.
        verdict_badge_class: Bootstrap badge classes for the verdict cell.
        frozen: ``True`` when this row was anonymized by the scoreboard freeze.
    """

    submission_id: str
    created_at: datetime
    team: str
    problem_label: str
    problem_name: str
    language_name: str
    language_icon: str
    verdict: str | None
    verdict_badge_class: str
    frozen: bool


@dataclass(frozen=True)
class ContestLiveFeedSnapshot:
    """Public contest live feed rows plus overflow metadata.

    Attributes:
        rows: Finalized submissions shown in the public live feed.
        limit: Configured maximum number of rows returned to the browser.
        has_more: True when older finalized submissions exist beyond ``rows``.
    """

    rows: list[LiveFeedRow]
    limit: int
    has_more: bool


def _active_judgment_subquery() -> Subquery:
    """Most-recent non-superseded judgment timestamp per submission."""
    return (
        select(
            submission_judgments_table.c.submission_id,
            func.max(submission_judgments_table.c.created_at).label("max_created_at"),
        )
        .where(submission_judgments_table.c.status != _SUPERSEDED)
        .group_by(submission_judgments_table.c.submission_id)
        .subquery()
    )


async def build_contest_live_feed_snapshot(session: AsyncSession, contest: Contest) -> ContestLiveFeedSnapshot:
    """Return the newest finalized team submissions for *contest*, blackout-aware.

    The limit is applied in SQL against the active judgment join with one extra
    row fetched to detect whether older finalized submissions exist beyond the cap.

    Args:
        session: Active async database session.
        contest: The contest whose feed is requested.

    Returns:
        ContestLiveFeedSnapshot: Rows and overflow metadata for the live feed.
    """
    active_j = _active_judgment_subquery()

    stmt = (
        select(
            submissions_table.c.id,
            submissions_table.c.created_at,
            submissions_table.c.timestamp_seconds,
            users_table.c.fullname,
            sites_table.c.sitename,
            problems_table.c.ordinal,
            problems_table.c.title,
            languages_table.c.name,
            languages_table.c.icon,
            submission_judgments_table.c.final_verdict,
        )
        .select_from(
            submissions_table.join(
                problems_table,
                submissions_table.c.problem_id == problems_table.c.id,
            )
            .join(users_table, submissions_table.c.team_id == users_table.c.id)
            .outerjoin(sites_table, users_table.c.site_id == sites_table.c.id)
            .join(languages_table, submissions_table.c.language_id == languages_table.c.id)
            .join(active_j, active_j.c.submission_id == submissions_table.c.id)
            .join(
                submission_judgments_table,
                and_(
                    submission_judgments_table.c.submission_id == submissions_table.c.id,
                    submission_judgments_table.c.created_at == active_j.c.max_created_at,
                ),
            )
        )
        .where(
            problems_table.c.contest_id == contest.id,
            users_table.c.role == RoleEnum.TEAM.value,
            submission_judgments_table.c.final_verdict.isnot(None),
        )
        .order_by(submissions_table.c.created_at.desc())
        .limit(CONTEST_LIVE_FEED_LIMIT + 1)
    )

    rows = (await session.execute(stmt)).all()
    has_more = len(rows) > CONTEST_LIVE_FEED_LIMIT
    rows = rows[:CONTEST_LIVE_FEED_LIMIT]
    frozen_active = contest.is_scoreboard_frozen
    freeze_after_seconds = contest.stop_updating_scoreboard * 60

    feed: list[LiveFeedRow] = []
    for row in rows:
        verdict: str | None = row.final_verdict
        is_frozen = frozen_active and (row.timestamp_seconds or 0) > freeze_after_seconds
        if is_frozen:
            # Anonymize team identity and mask the verdict so no post-freeze activity leaks.
            feed.append(
                LiveFeedRow(
                    submission_id=row.id,
                    created_at=row.created_at,
                    team=_FROZEN_TEAM_PLACEHOLDER,
                    problem_label=_label(row.ordinal),
                    problem_name=row.title,
                    language_name=row.name,
                    language_icon=row.icon,
                    verdict=None,
                    verdict_badge_class=_FROZEN_BADGE_CLASS,
                    frozen=True,
                )
            )
            continue
        feed.append(
            LiveFeedRow(
                submission_id=row.id,
                created_at=row.created_at,
                team=format_site_identity(row.sitename, row.fullname),
                problem_label=_label(row.ordinal),
                problem_name=row.title,
                language_name=row.name,
                language_icon=row.icon,
                verdict=verdict,
                verdict_badge_class=contest_verdict_badge_class(verdict or "", contest),
                frozen=False,
            )
        )
    return ContestLiveFeedSnapshot(rows=feed, limit=CONTEST_LIVE_FEED_LIMIT, has_more=has_more)
