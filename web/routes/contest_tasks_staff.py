#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep

from shared.enumerations import RoleEnum, TaskType
from shared.services.lock_service import get_lock
from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.models.users import User
from web.routes.contest_tasks_helpers import _RELEASE_ALLOWED, _SOURCE_ALLOWED, _STAFF_ONLY
from web.services.task_service import (
    ContestNotRunningError,
    ForbiddenTaskActionError,
    TaskAcquisitionTimeoutError,
    TaskAlreadyAcquiredError,
    TaskAlreadyFinishedError,
    TaskLockUnavailableError,
    TaskNotAcquiredByActorError,
    acquire_task,
    finish_task,
    get_task,
    release_task,
)

router = APIRouter(prefix="/c/{slug}/tasks", tags=["contest_tasks"])


@router.post(
    "/{task_id}/acquire",
    response_class=HTMLResponse,
    response_model=None,
    name="contest_tasks_acquire",
)
async def acquire(
    request: Request,
    flash: FlashDep,
    task_id: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> Response:
    ensure_allowed_role(ctx.actor, _STAFF_ONLY)
    assert isinstance(ctx.actor, User)
    slug = ctx.contest.login_slug

    task = await get_task(ctx.session, ctx.contest, task_id)
    if task is None:
        flash("Task not found.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/tasks/", status_code=303)

    try:
        if not request.app.state.valkey_runtime.is_available:
            flash("Lock service unavailable. Possible double work/rework.", FlashCategory.WARNING)
            return RedirectResponse(url=f"/c/{slug}/tasks/?open={task_id}", status_code=303)
        await acquire_task(ctx.session, ctx.contest, ctx.actor, task, request.app.state.valkey_runtime)
        await ctx.session.commit()
        return RedirectResponse(url=f"/c/{slug}/tasks/?open={task_id}", status_code=303)
    except TaskAlreadyFinishedError:
        flash("This task has already been finished.", FlashCategory.WARNING)
    except TaskAlreadyAcquiredError:
        flash("This task was just acquired by another staff member.", FlashCategory.WARNING)
    except ContestNotRunningError:
        flash("Tasks can only be acquired while the contest is running.", FlashCategory.DANGER)
    except TaskLockUnavailableError:
        flash("Lock service unavailable. Possible double work/rework.", FlashCategory.WARNING)
        return RedirectResponse(url=f"/c/{slug}/tasks/?open={task_id}", status_code=303)

    return RedirectResponse(url=f"/c/{slug}/tasks/", status_code=303)


@router.post(
    "/{task_id}/finish",
    response_class=HTMLResponse,
    response_model=None,
    name="contest_tasks_finish",
)
async def finish(
    request: Request,
    flash: FlashDep,
    task_id: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> Response:
    ensure_allowed_role(ctx.actor, _STAFF_ONLY)
    assert isinstance(ctx.actor, User)
    slug = ctx.contest.login_slug

    task = await get_task(ctx.session, ctx.contest, task_id)
    if task is None:
        flash("Task not found.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/tasks/", status_code=303)

    try:
        await finish_task(ctx.session, ctx.contest, ctx.actor, task, request.app.state.valkey_runtime)
        await ctx.session.commit()
        flash("Task marked as finished.", FlashCategory.SUCCESS)
    except TaskAlreadyFinishedError:
        flash("This task has already been finished.", FlashCategory.WARNING)
    except TaskNotAcquiredByActorError:
        flash("You must hold the task lock to finish it.", FlashCategory.DANGER)
    except TaskAcquisitionTimeoutError:
        await ctx.session.commit()
        flash(
            "Your acquisition window expired. The lock was released — you may re-acquire the task.",
            FlashCategory.WARNING,
        )
    except ContestNotRunningError:
        flash("Tasks can only be finished while the contest is running.", FlashCategory.DANGER)

    return RedirectResponse(url=f"/c/{slug}/tasks/", status_code=303)


@router.post(
    "/{task_id}/release",
    response_class=HTMLResponse,
    response_model=None,
    name="contest_tasks_release",
)
async def release(
    request: Request,
    flash: FlashDep,
    task_id: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> Response:
    ensure_allowed_role(ctx.actor, _RELEASE_ALLOWED)
    slug = ctx.contest.login_slug

    task = await get_task(ctx.session, ctx.contest, task_id)
    if task is None:
        flash("Task not found.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/tasks/", status_code=303)

    try:
        if not request.app.state.valkey_runtime.is_available:
            flash("Lock service unavailable. Possible double work/rework.", FlashCategory.WARNING)
            return RedirectResponse(url=f"/c/{slug}/tasks/", status_code=303)
        await release_task(ctx.session, ctx.contest, ctx.actor, task, request.app.state.valkey_runtime)
        await ctx.session.commit()
        flash("Task lock released.", FlashCategory.INFO)
    except TaskNotAcquiredByActorError:
        flash("You do not hold the lock on this task.", FlashCategory.DANGER)
    except ForbiddenTaskActionError:
        flash("You are not allowed to release this task.", FlashCategory.DANGER)

    return RedirectResponse(url=f"/c/{slug}/tasks/", status_code=303)


@router.get("/{task_id}/source", response_model=None, name="contest_tasks_source")
async def download_source(
    request: Request,
    flash: FlashDep,
    task_id: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> Response:
    ensure_allowed_role(ctx.actor, _SOURCE_ALLOWED)
    slug = ctx.contest.login_slug

    task = await get_task(ctx.session, ctx.contest, task_id)
    if task is None:
        flash("Task not found.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/tasks/", status_code=303)

    if task.type != TaskType.PRINT:
        flash("Source code is only available for PRINT tasks.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/tasks/", status_code=303)

    actor = ctx.actor
    if isinstance(actor, User) and actor.role == RoleEnum.STAFF and request.app.state.valkey_runtime.is_available:
        lock = await get_lock(
            request.app.state.valkey_runtime,
            kind="task",
            contest_id=ctx.contest.id,
            resource_id=task.id,
        )
        if lock is None or lock.holder_id != actor.id:
            flash("You must hold the task lock to download source code.", FlashCategory.DANGER)
            return RedirectResponse(url=f"/c/{slug}/tasks/", status_code=303)

    content = task.source_code.encode("utf-8")
    filename = f"print-task-{task_id[:8]}.txt"
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
