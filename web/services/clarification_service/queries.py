#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Read/query helpers for contest clarifications."""

from __future__ import annotations

from typing import Literal, cast

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from shared.services.lock_service import LockClient, get_locks
from web.models.clarification import Clarification
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
    """Count answered clarifications that the requesting team has not read."""
    result = await session.execute(
        select(func.count(Clarification.id))
        .join(User, Clarification.team_id == User.id)
        .where(
            User.contest_id == contest.id,
            Clarification.team_id == team_id,
            Clarification.answered_at.is_not(None),
            Clarification.answer_read_at.is_(None),
            Clarification.hidden.is_(False),
        )
    )
    return int(result.scalar() or 0)


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
    return (
        merge_clarification_views(
            clarifications,
            actor=actor,
            show_judge=show_judge,
            lock_batch=lock_batch,
        ),
        lock_batch.service_available,
    )
