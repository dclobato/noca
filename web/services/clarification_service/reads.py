#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Acknowledgement of clarifications a team has seen.

A team is notified about two different things through one list: answers to questions it
asked, and announcements published to the whole contest. Their read state is stored
differently -- an answer sets its own row's ``answer_read_at``, while an announcement is
read by many teams and therefore needs a per-team marker row -- so both are acknowledged
here, through one call, so a caller can never record half of what it rendered.
"""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import clarification_reads as clarification_reads_table
from shared.enumerations import RoleEnum
from web.models._base import _utcnow
from web.models.clarification import Clarification
from web.models.contest import Contest
from web.models.users import User

from .errors import ForbiddenClarificationActionError
from .queries import announcement_visible_to_teams


async def _mark_own_answers_read(
    session: AsyncSession,
    actor: User,
    clarification_ids: Collection[str],
) -> int:
    """Stamp ``answer_read_at`` on the team's own answered questions."""
    result = await session.execute(
        update(Clarification)
        .where(
            Clarification.id.in_(clarification_ids),
            Clarification.team_id == actor.id,
            Clarification.is_announcement.is_(False),
            Clarification.answered_at.is_not(None),
            Clarification.answer_read_at.is_(None),
            Clarification.hidden.is_(False),
        )
        .values(answer_read_at=_utcnow())
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def _mark_announcements_read(
    session: AsyncSession,
    contest: Contest,
    actor: User,
    clarification_ids: Collection[str],
) -> int:
    """Insert this team's read markers for the supplied contest announcements."""
    visible = await session.execute(
        select(Clarification.id)
        .join(User, Clarification.team_id == User.id)
        .where(
            Clarification.id.in_(clarification_ids),
            announcement_visible_to_teams(),
            User.contest_id == contest.id,
        )
    )
    announcement_ids = list(visible.scalars().all())
    if not announcement_ids:
        return 0

    now = _utcnow()
    # A full page load can race the 60 s HTMX refresh, and two tabs race each other, so a
    # duplicate acknowledgement is normal traffic rather than an error.
    result = await session.execute(
        pg_insert(clarification_reads_table)
        .values(
            [
                {"clarification_id": announcement_id, "user_id": actor.id, "read_at": now}
                for announcement_id in announcement_ids
            ]
        )
        .on_conflict_do_nothing(index_elements=["clarification_id", "user_id"])
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def mark_clarification_answers_read(
    session: AsyncSession,
    contest: Contest,
    actor: User,
    clarification_ids: Collection[str],
) -> int:
    """Mark rendered answers and announcements as read by the viewing team.

    Args:
        session: Active database session.
        contest: Contest containing the clarifications.
        actor: Team acknowledging what it was shown.
        clarification_ids: Identifiers that were rendered to the team.

    Returns:
        The number of notifications newly marked as read, across both kinds.

    Raises:
        ForbiddenClarificationActionError: If the actor is not a team in the
            supplied contest.
    """
    if actor.role != RoleEnum.TEAM or actor.contest_id != contest.id:
        raise ForbiddenClarificationActionError("Only the requesting team may acknowledge clarification answers.")

    unique_ids = frozenset(clarification_ids)
    if not unique_ids:
        return 0

    changed = await _mark_own_answers_read(session, actor, unique_ids)
    changed += await _mark_announcements_read(session, contest, actor, unique_ids)
    await session.flush()
    return changed
