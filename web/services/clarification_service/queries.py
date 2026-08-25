#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Read/query helpers for contest clarifications."""

from __future__ import annotations

from collections.abc import Collection
from typing import Literal, cast

from sqlalchemy import ColumnElement, Select, and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from shared.services.lock_service import LockClient, get_locks
from web.models.clarification import Clarification, ClarificationRead
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.users import UberAdmin, User

from .views import ClarificationView, merge_clarification_views

ClarificationSort = Literal["time_asc", "time_desc", "problem_asc", "problem_desc"]

_ALLOWED_SORTS: frozenset[str] = frozenset({"time_asc", "time_desc", "problem_asc", "problem_desc"})


def normalize_clarification_sort(value: str | None) -> ClarificationSort:
    """Normalize a clarification list ``sort_by`` query parameter."""
    if value in _ALLOWED_SORTS:
        return cast(ClarificationSort, value)
    return "time_desc"


def _apply_clarification_sort(
    stmt: Select[tuple[Clarification]], sort_by: ClarificationSort
) -> Select[tuple[Clarification]]:
    """Order clarifications by *sort_by*; problem sort ties break on newest first.

    General clarifications have no problem, so they group last in both problem
    directions instead of being scattered by the database's default NULL ordering.
    """
    if sort_by == "time_asc":
        return stmt.order_by(Clarification.created_at.asc())
    if sort_by == "problem_asc":
        return stmt.order_by(Problem.ordinal.asc().nulls_last(), Clarification.created_at.desc())
    if sort_by == "problem_desc":
        return stmt.order_by(Problem.ordinal.desc().nulls_last(), Clarification.created_at.desc())
    return stmt.order_by(Clarification.created_at.desc())


def announcement_visible_to_teams() -> ColumnElement[bool]:
    """Return the condition under which an announcement is shown to a contest's teams.

    This is the same visibility a team's list already applies -- public, not hidden -- and
    it is written once because three surfaces must agree on it: the list, the dashboard
    counter, and the acknowledgement endpoint. If the counter were more permissive than
    the list, a team could be badged about a row it cannot see and could acknowledge a row
    it was never shown. ``create_announcement`` always publishes publicly, but a restored
    backup carries whatever the archive held, so the rule is enforced rather than assumed.

    Returns:
        A SQLAlchemy condition over :class:`Clarification`.
    """
    return and_(
        Clarification.is_announcement.is_(True),
        Clarification.is_contest_public.is_(True),
        Clarification.hidden.is_(False),
    )


async def get_clarification(
    session: AsyncSession,
    contest: Contest,
    clarification_id: str,
) -> Clarification | None:
    """Fetch a single clarification scoped to the given contest."""
    result = await session.execute(
        select(Clarification)
        .join(User, Clarification.team_id == User.id)
        .where(Clarification.id == clarification_id, User.contest_id == contest.id)
    )
    return result.scalar_one_or_none()


async def count_pending_clarifications(
    session: AsyncSession,
    contest: Contest,
    team_id: str | None = None,
) -> int:
    """Count visible unanswered clarifications in a contest."""
    stmt = (
        select(func.count(Clarification.id))
        .join(User, Clarification.team_id == User.id)
        .where(
            and_(
                User.contest_id == contest.id,
                Clarification.answered_at.is_(None),
                Clarification.hidden.is_(False),
            )
        )
    )
    if team_id is not None:
        stmt = stmt.where(Clarification.team_id == team_id)
    return int((await session.execute(stmt)).scalar() or 0)


async def count_unread_clarification_answers(
    session: AsyncSession,
    contest: Contest,
    team_id: str,
) -> int:
    """Count answered clarifications that the requesting team has not read.

    Announcements are excluded even though they are answered rows: they are stored with
    ``team_id`` set to their author, so a judge later changed to TEAM would otherwise see
    their own announcements counted here *and* by
    :func:`count_unread_announcements`. Keeping the two summands disjoint is what makes
    :func:`count_unread_team_clarifications` a sum rather than a union.
    """
    result = await session.execute(
        select(func.count(Clarification.id))
        .join(User, Clarification.team_id == User.id)
        .where(
            User.contest_id == contest.id,
            Clarification.team_id == team_id,
            Clarification.is_announcement.is_(False),
            Clarification.answered_at.is_not(None),
            Clarification.answer_read_at.is_(None),
            Clarification.hidden.is_(False),
        )
    )
    return int(result.scalar() or 0)


async def count_unread_announcements(
    session: AsyncSession,
    contest: Contest,
    team_id: str,
) -> int:
    """Count contest announcements the given team has not acknowledged.

    Unlike an answer, an announcement matches *every* team of its contest, so the query
    cannot rely on the caller's id appearing in the row. It therefore checks the target
    user's own contest membership explicitly; without that, a ``team_id`` belonging to
    another contest would be handed this contest's announcements.

    Args:
        session: Active database session.
        contest: Contest whose announcements are counted.
        team_id: Identifier of the team the count is for.

    Returns:
        The number of visible announcements with no read marker for that team.
    """
    is_contest_team = exists().where(
        User.id == team_id,
        User.contest_id == contest.id,
        User.role == RoleEnum.TEAM,
    )
    already_read = exists().where(
        ClarificationRead.clarification_id == Clarification.id,
        ClarificationRead.user_id == team_id,
    )
    result = await session.execute(
        select(func.count(Clarification.id))
        .join(User, Clarification.team_id == User.id)
        .where(
            User.contest_id == contest.id,
            announcement_visible_to_teams(),
            is_contest_team,
            ~already_read,
        )
    )
    return int(result.scalar() or 0)


async def count_unread_team_clarifications(
    session: AsyncSession,
    contest: Contest,
    team_id: str,
) -> int:
    """Count everything in the contest's clarifications the team has not yet seen.

    The dashboard shows one Clarifications counter, so unread answers to the team's own
    questions and unread announcements are merged into one number. The two summands are
    disjoint by construction: an announcement never satisfies the answer branch.

    Args:
        session: Active database session.
        contest: Contest the team belongs to.
        team_id: Identifier of the team the count is for.

    Returns:
        Unread own answers plus unread announcements.
    """
    answers = await count_unread_clarification_answers(session, contest, team_id)
    announcements = await count_unread_announcements(session, contest, team_id)
    return answers + announcements


async def get_read_announcement_ids(
    session: AsyncSession,
    team_id: str,
    clarification_ids: Collection[str],
) -> frozenset[str]:
    """Return which of *clarification_ids* the given team has already read.

    Args:
        session: Active database session.
        team_id: Identifier of the reading team.
        clarification_ids: Announcement identifiers to look up.

    Returns:
        The subset that carries a read marker for that team; empty when nothing is asked.
    """
    unique_ids = frozenset(clarification_ids)
    if not unique_ids:
        return frozenset()
    result = await session.execute(
        select(ClarificationRead.clarification_id).where(
            ClarificationRead.user_id == team_id,
            ClarificationRead.clarification_id.in_(unique_ids),
        )
    )
    return frozenset(result.scalars().all())


async def list_clarifications(
    session: AsyncSession,
    contest: Contest,
    actor: User | UberAdmin,
    lock_client: LockClient,
    sort_by: ClarificationSort = "time_desc",
) -> tuple[list[ClarificationView], bool]:
    """Return clarifications visible to the given actor."""
    base_stmt = (
        select(Clarification)
        .join(User, Clarification.team_id == User.id)
        .outerjoin(Problem, Clarification.problem_id == Problem.id)
        .where(User.contest_id == contest.id)
    )

    if isinstance(actor, UberAdmin) or actor.role == RoleEnum.ADMIN:
        stmt = base_stmt
        show_judge = True
    elif actor.role == RoleEnum.JUDGE:
        stmt = base_stmt
        show_judge = False
    else:
        stmt = base_stmt.where(
            Clarification.hidden == False,  # noqa: E712
            or_(
                Clarification.team_id == actor.id,
                Clarification.is_contest_public,
            ),
        )
        show_judge = False

    stmt = _apply_clarification_sort(stmt, sort_by)
    result = await session.execute(stmt)
    clarifications = list(result.scalars().all())
    lock_batch = await get_locks(
        lock_client,
        kind="clarification",
        contest_id=contest.id,
        resource_ids=[clarification.id for clarification in clarifications],
    )
    # Only a team has announcement read state; `is_announcement` is a column on the rows
    # already loaded, so this costs one extra statement and never a lazy load.
    read_announcement_ids: frozenset[str] = frozenset()
    if not isinstance(actor, UberAdmin) and actor.role == RoleEnum.TEAM:
        read_announcement_ids = await get_read_announcement_ids(
            session,
            actor.id,
            [clarification.id for clarification in clarifications if clarification.is_announcement],
        )
    return (
        merge_clarification_views(
            clarifications,
            actor=actor,
            show_judge=show_judge,
            lock_batch=lock_batch,
            read_announcement_ids=read_announcement_ids,
        ),
        lock_batch.service_available,
    )
