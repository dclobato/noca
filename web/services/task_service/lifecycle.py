#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Lifecycle operations for contest tasks."""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum, TaskType
from shared.services.lock_service import (
    LockClient,
    acquire_lock,
    force_release_lock,
    get_lock,
    release_lock,
)
from shared.timing import compute_timestamp_seconds
from web.models._base import _utcnow
from web.models.contest import Contest, Task
from web.models.problem import Problem
from web.models.users import UberAdmin, User

from .errors import (
    ContestNotRunningError,
    DuplicatePrintTaskError,
    ForbiddenTaskActionError,
    PrintRequestsDisabledError,
    TaskAlreadyAcquiredError,
    TaskAlreadyFinishedError,
    TaskLockUnavailableError,
    TaskNotAcquiredByActorError,
)

_EMPTY_SOURCE_HASH = hashlib.sha256(b"").hexdigest()


async def create_sos_task(session: AsyncSession, contest: Contest, actor: User) -> Task:
    """Create a new SOS task on behalf of a team."""
    if not contest.is_running:
        raise ContestNotRunningError("Tasks can only be created while the contest is running.")
    if actor.role != RoleEnum.TEAM:
        raise ForbiddenTaskActionError("Only team members may create SOS tasks.")

    now = _utcnow()
    task = Task(
        team_id=actor.id,
        type=TaskType.SOS,
        problem_id=None,
        source_code="",
        source_hash=_EMPTY_SOURCE_HASH,
        source_size_bytes=0,
        created_at=now,
        created_timestamp_seconds=compute_timestamp_seconds(contest.start_time, now),
    )
    session.add(task)
    await session.flush()
    return task


async def create_print_task(
    session: AsyncSession,
    contest: Contest,
    actor: User,
    *,
    problem_id: str,
    source_code: str,
) -> Task:
    """Create a PRINT task for a team."""
    if not contest.is_running:
        raise ContestNotRunningError("Tasks can only be created while the contest is running.")
    if actor.role != RoleEnum.TEAM:
        raise ForbiddenTaskActionError("Only team members may create print tasks.")
    if not contest.allow_print_requests:
        raise PrintRequestsDisabledError("Print requests are disabled for this contest.")

    result = await session.execute(select(Problem).where(Problem.id == problem_id, Problem.contest_id == contest.id))
    if result.scalar_one_or_none() is None:
        raise ValueError(f"Problem '{problem_id}' not found in contest '{contest.id}'.")

    encoded = source_code.encode()
    if contest.max_problem_file_size_bytes > 0 and len(encoded) > contest.max_problem_file_size_bytes:
        raise ValueError(
            f"Source code size ({len(encoded)} bytes) exceeds the contest limit "
            f"({contest.max_problem_file_size_bytes} bytes)."
        )

    source_hash = hashlib.sha256(encoded).hexdigest()
    duplicate = await session.execute(
        select(Task).where(
            Task.type == TaskType.PRINT,
            Task.team_id == actor.id,
            Task.problem_id == problem_id,
            Task.source_hash == source_hash,
            Task.finished_at.is_(None),
        )
    )
    if duplicate.scalar_one_or_none() is not None:
        raise DuplicatePrintTaskError("A pending print task for this source code already exists.")

    now = _utcnow()
    task = Task(
        team_id=actor.id,
        type=TaskType.PRINT,
        problem_id=problem_id,
        source_code=source_code,
        source_hash=source_hash,
        source_size_bytes=len(encoded),
        created_at=now,
        created_timestamp_seconds=compute_timestamp_seconds(contest.start_time, now),
    )
    session.add(task)
    await session.flush()
    return task


async def create_balloon_task(
    session: AsyncSession,
    *,
    contest: Contest,
    problem_id: str,
    team_id: str,
    task_type: TaskType = TaskType.BALLOON,
) -> Task:
    """Create a balloon delivery task triggered by an accepted judgment."""
    now = _utcnow()
    task = Task(
        team_id=team_id,
        type=task_type,
        problem_id=problem_id,
        source_code="",
        source_hash=_EMPTY_SOURCE_HASH,
        source_size_bytes=0,
        created_at=now,
        created_timestamp_seconds=compute_timestamp_seconds(contest.start_time, now),
    )
    session.add(task)
    await session.flush()
    return task


async def acquire_task(
    session: AsyncSession,
    contest: Contest,
    actor: User,
    task: Task,
    lock_client: LockClient,
) -> Task:
    """Acquire a task lock so a staff member may handle it."""
    if not contest.is_running:
        raise ContestNotRunningError("Tasks can only be acquired while the contest is running.")
    if actor.role != RoleEnum.STAFF:
        raise ForbiddenTaskActionError("Only staff members may acquire tasks.")
    if task.finished_at is not None:
        raise TaskAlreadyFinishedError("This task has already been finished.")

    timeout_minutes = contest.tasks_timeout_minutes
    ttl_seconds = contest.remaining_time_seconds if timeout_minutes == 0 else timeout_minutes * 60
    acquired = await acquire_lock(
        lock_client,
        kind="task",
        contest_id=contest.id,
        resource_id=task.id,
        holder_id=actor.id,
        holder_role=actor.role.value,
        ttl_seconds=ttl_seconds,
    )
    if acquired is None:
        raise TaskLockUnavailableError("Task locks are currently unavailable.")
    if not acquired:
        raise TaskAlreadyAcquiredError("This task is already acquired by another staff member.")
    return task


async def release_task(
    session: AsyncSession,
    contest: Contest,
    actor: User | UberAdmin,
    task: Task,
    lock_client: LockClient,
) -> Task:
    """Release a staff member's lock on a task without finishing it."""
    lock = await get_lock(lock_client, kind="task", contest_id=contest.id, resource_id=task.id)
    if lock is None:
        if isinstance(actor, UberAdmin) or actor.role == RoleEnum.ADMIN:
            return task
        if actor.role != RoleEnum.STAFF:
            raise ForbiddenTaskActionError("Only staff members and admins may release task locks.")
        raise TaskNotAcquiredByActorError("You do not hold the lock on this task.")

    if not isinstance(actor, UberAdmin) and actor.role not in (RoleEnum.ADMIN, RoleEnum.STAFF):
        raise ForbiddenTaskActionError("Only staff members and admins may release task locks.")
    if not isinstance(actor, UberAdmin) and actor.role == RoleEnum.STAFF and lock.holder_id != actor.id:
        raise TaskNotAcquiredByActorError("You do not hold the lock on this task.")

    if isinstance(actor, UberAdmin) or actor.role == RoleEnum.ADMIN:
        await force_release_lock(lock_client, kind="task", contest_id=contest.id, resource_id=task.id)
    else:
        await release_lock(
            lock_client,
            kind="task",
            contest_id=contest.id,
            resource_id=task.id,
            holder_id=actor.id,
        )
    return task


async def finish_task(
    session: AsyncSession,
    contest: Contest,
    actor: User,
    task: Task,
    lock_client: LockClient,
) -> Task:
    """Mark a task as finished by the staff member who holds the lock."""
    if not contest.is_running:
        raise ContestNotRunningError("Tasks can only be finished while the contest is running.")
    if actor.role != RoleEnum.STAFF:
        raise ForbiddenTaskActionError("Only staff members may finish tasks.")
    if task.finished_at is not None:
        raise TaskAlreadyFinishedError("This task has already been finished.")

    lock = await get_lock(lock_client, kind="task", contest_id=contest.id, resource_id=task.id)
    if lock is not None and lock.holder_id != actor.id:
        raise TaskNotAcquiredByActorError("You must acquire this task before finishing it.")
    if lock is None and getattr(lock_client, "is_available", True):
        raise TaskNotAcquiredByActorError("You must acquire this task before finishing it.")

    now = _utcnow()
    task.staff_id = actor.id
    task.finished_at = now
    task.finished_timestamp_seconds = compute_timestamp_seconds(contest.start_time, now)
    await session.flush()
    if lock is not None:
        await release_lock(
            lock_client,
            kind="task",
            contest_id=contest.id,
            resource_id=task.id,
            holder_id=actor.id,
        )
    return task
