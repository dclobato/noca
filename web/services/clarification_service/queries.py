#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Read/query helpers for contest clarifications."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from shared.services.lock_service import LockClient, get_locks
from web.models.clarification import Clarification
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.users import UberAdmin, User

from .views import ClarificationView, merge_clarification_views


async def get_clarification(
    session: AsyncSession,
    contest: Contest,
    clarification_id: str,
) -> Clarification | None:
    """Fetch a single clarification scoped to the given contest."""
    result = await session.execute(
        select(Clarification)
        .join(Problem, Clarification.problem_id == Problem.id)
        .where(Clarification.id == clarification_id, Problem.contest_id == contest.id)
    )
    return result.scalar_one_or_none()


async def list_clarifications(
    session: AsyncSession,
    contest: Contest,
    actor: User | UberAdmin,
    lock_client: LockClient,
) -> tuple[list[ClarificationView], bool]:
    """Return clarifications visible to the given actor."""
    base_stmt = (
        select(Clarification)
        .join(Problem, Clarification.problem_id == Problem.id)
        .where(Problem.contest_id == contest.id)
        .order_by(Clarification.created_at.desc())
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
