#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Read/query helpers for contest tasks."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from shared.services.lock_service import LockClient, get_locks
from web.models.contest import Contest, Task
from web.models.problem import Problem
from web.models.users import UberAdmin, User

from .errors import ForbiddenTaskActionError
from .views import TaskView, merge_task_views


def task_belongs_to_contest_via_team(contest: Contest, task_alias: type[Task]) -> Any:
    """Return a clause that ties a NULL-problem task to the contest through its team."""
    from web.models.users import User as UserModel

    return (
        select(UserModel.id)
        .where(
            UserModel.id == task_alias.team_id,
            UserModel.contest_id == contest.id,
        )
        .exists()
    )


async def get_task(
    session: AsyncSession,
    contest: Contest,
    task_id: str,
) -> Task | None:
    """Fetch a single task scoped to the given contest."""
    result = await session.execute(
        select(Task)
        .outerjoin(Problem, Task.problem_id == Problem.id)
        .where(
            Task.id == task_id,
            or_(
                Problem.contest_id == contest.id,
                and_(Task.problem_id.is_(None), task_belongs_to_contest_via_team(contest, Task)),
            ),
        )
    )
    return result.scalar_one_or_none()


async def list_tasks(
    session: AsyncSession,
    contest: Contest,
    actor: User | UberAdmin,
    lock_client: LockClient,
) -> tuple[list[TaskView], bool]:
    """Return tasks visible to the given actor."""
    from web.models.users import User as UserModel

    base_stmt = (
        select(Task)
        .outerjoin(Problem, Task.problem_id == Problem.id)
        .outerjoin(UserModel, Task.team_id == UserModel.id)
        .where(
            or_(
                Problem.contest_id == contest.id,
                and_(Task.problem_id.is_(None), UserModel.contest_id == contest.id),
            )
        )
        .order_by(Task.created_at.asc())
    )

    if isinstance(actor, UberAdmin) or actor.role in (RoleEnum.ADMIN, RoleEnum.JUDGE, RoleEnum.STAFF):
        stmt = base_stmt
    elif actor.role == RoleEnum.TEAM:
        stmt = base_stmt.where(Task.team_id == actor.id)
    else:
        raise ForbiddenTaskActionError("Your role does not have permission to list tasks.")

    result = await session.execute(stmt)
    tasks = list(result.scalars().all())
    lock_batch = await get_locks(
        lock_client,
        kind="task",
        contest_id=contest.id,
        resource_ids=[task.id for task in tasks],
    )
    return merge_task_views(tasks, actor=actor, lock_batch=lock_batch), lock_batch.service_available
