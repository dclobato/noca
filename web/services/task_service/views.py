#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Task service DTOs and view helpers."""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from shared.enumerations import TaskType
from shared.services.lock_service import LockBatchResult
from web.models.contest import Task
from web.models.users import UberAdmin, User


@dataclass
class TaskView:
    """Role-scoped projection of a task."""

    id: str
    type: TaskType
    team_id: str
    staff_id: str | None
    problem_id: str | None
    finished_at: datetime.datetime | None
    acquired_at: datetime.datetime | None
    source_size_bytes: int
    created_at: datetime.datetime
    created_timestamp_seconds: int
    acquired_by_me: bool


def to_view(task: Task, *, actor_id: str | None) -> TaskView:
    """Project one task to its role-scoped DTO."""
    return TaskView(
        id=task.id,
        type=task.type,
        team_id=task.team_id,
        staff_id=task.staff_id,
        problem_id=task.problem_id,
        finished_at=task.finished_at,
        acquired_at=None,
        source_size_bytes=task.source_size_bytes,
        created_at=task.created_at,
        created_timestamp_seconds=task.created_timestamp_seconds,
        acquired_by_me=task.staff_id is not None and task.staff_id == actor_id,
    )


def merge_task_views(tasks: list[Task], *, actor: User | UberAdmin, lock_batch: LockBatchResult) -> list[TaskView]:
    """Merge database task rows with lock state for UI consumption."""
    actor_id = None if isinstance(actor, UberAdmin) else actor.id
    views: list[TaskView] = []
    for task in tasks:
        view = to_view(task, actor_id=actor_id)
        if task.finished_at is not None:
            views.append(view)
            continue
        lock = lock_batch.locks_by_resource_id.get(task.id)
        if lock is None:
            views.append(view)
            continue
        view.staff_id = lock.holder_id
        view.acquired_at = lock.acquired_at
        view.acquired_by_me = actor_id is not None and lock.holder_id == actor_id
        views.append(view)
    return views
