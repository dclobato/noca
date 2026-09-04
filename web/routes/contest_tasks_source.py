#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Raw-download and printable-view routes for PRINT tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep

from shared.enumerations import TaskType
from shared.services.lock_service import LockState, get_lock
from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.models.contest import Task
from web.models.users import User
from web.routes.contest_admin_problem_helpers import _label
from web.routes.contest_tasks_helpers import _SOURCE_ALLOWED
from web.services.task_service import can_force_release_tasks, can_view_tasks, get_task_with_details

router = APIRouter(prefix="/c/{slug}/tasks", tags=["contest_tasks"])


@dataclass(frozen=True, slots=True)
class _SourceTaskAccess:
    """Authorized source task plus its current lock state."""

    task: Task
    lock: LockState | None
    lock_service_available: bool


def _tasks_redirect(slug: str) -> RedirectResponse:
    """Return the canonical redirect to the contest task list."""
    return RedirectResponse(url=f"/c/{slug}/tasks/", status_code=303)


async def _load_source_task(
    request: Request,
    flash: FlashDep,
    task_id: str,
    ctx: ContestContext,
    *,
    lock_failure_message: str,
    resolve_handler_lock: bool,
) -> _SourceTaskAccess | RedirectResponse:
    """Load one PRINT task and enforce the shared source-access policy."""
    ensure_allowed_role(ctx.actor, _SOURCE_ALLOWED)
    if not can_view_tasks(ctx.actor, ctx.contest):
        raise HTTPException(status_code=403)

    slug = ctx.contest.login_slug
    task = await get_task_with_details(ctx.session, ctx.contest, task_id)
    if task is None:
        flash("Task not found.", FlashCategory.DANGER)
        return _tasks_redirect(slug)
    if task.type != TaskType.PRINT:
        flash("Source code is only available for PRINT tasks.", FlashCategory.DANGER)
        return _tasks_redirect(slug)

    actor = ctx.actor
    actor_needs_lock = isinstance(actor, User) and not can_force_release_tasks(actor)
    lock_service_available = request.app.state.valkey_runtime.is_available
    lock = None
    should_load_lock = actor_needs_lock or (resolve_handler_lock and task.finished_at is None)
    if lock_service_available and should_load_lock:
        lock = await get_lock(
            request.app.state.valkey_runtime,
            kind="task",
            contest_id=ctx.contest.id,
            resource_id=task.id,
        )
    if actor_needs_lock and lock_service_available and (lock is None or lock.holder_id != actor.id):
        flash(lock_failure_message, FlashCategory.DANGER)
        return _tasks_redirect(slug)

    return _SourceTaskAccess(
        task=task,
        lock=lock,
        lock_service_available=lock_service_available,
    )


def _contest_time(timestamp_seconds: int) -> str:
    """Format a contest-relative timestamp as ``HH:MM:SS``."""
    hours, remainder = divmod(max(0, timestamp_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _created_at_text(created_at: datetime, timezone_name: str) -> str:
    """Format a task timestamp in the contest timezone."""
    aware_created_at = created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=UTC)
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("UTC")
        timezone_name = "UTC"
    local_created_at = aware_created_at.astimezone(timezone)
    return f"{local_created_at:%Y-%m-%d %H:%M:%S} ({timezone_name})"


def _user_label(user: User | None) -> str:
    """Return a printable user identity or an explicit missing state."""
    if user is None:
        return "Not recorded"
    return f"{user.fullname or user.username} ({user.username})"


async def _handler_label(ctx: ContestContext, access: _SourceTaskAccess) -> str:
    """Resolve the persisted finisher or active task-lock holder."""
    if access.task.finished_at is not None:
        return _user_label(access.task.staff)
    if not access.lock_service_available:
        return "Unavailable — lock service offline"
    if access.lock is None:
        return "Unassigned"
    handler = await ctx.session.get(User, access.lock.holder_id)
    if handler is None:
        return f"Unknown user ({access.lock.holder_id[:8]})"
    return _user_label(handler)


@router.get("/{task_id}/source", response_model=None, name="contest_tasks_source")
async def download_source(
    request: Request,
    flash: FlashDep,
    task_id: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> Response:
    """Download the unformatted source text for one PRINT task."""
    access = await _load_source_task(
        request,
        flash,
        task_id,
        ctx,
        lock_failure_message="You must hold the task lock to download source code.",
        resolve_handler_lock=False,
    )
    if isinstance(access, RedirectResponse):
        return access

    filename = f"print-task-{task_id[:8]}.txt"
    return Response(
        content=access.task.source_code.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{task_id}/printout",
    response_class=HTMLResponse,
    response_model=None,
    name="contest_tasks_printout",
)
async def view_printout(
    request: Request,
    flash: FlashDep,
    task_id: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> Response:
    """Render a printer-friendly delivery sheet and source listing."""
    access = await _load_source_task(
        request,
        flash,
        task_id,
        ctx,
        lock_failure_message="You must hold the task lock to open the printout.",
        resolve_handler_lock=True,
    )
    if isinstance(access, RedirectResponse):
        return access

    task = access.task
    problem = task.problem
    source_lines = task.source_code.splitlines() or [""]
    problem_text = f"{_label(problem.ordinal)} — {problem.title}" if problem is not None else "Not assigned"
    return cast(
        Response,
        request.app.state.templates.TemplateResponse(
            request=request,
            name="contest/task_print.html",
            context={
                "contest": ctx.contest,
                "task": task,
                "team": task.team,
                "problem": problem,
                "problem_text": problem_text,
                "handler_label": await _handler_label(ctx, access),
                "contest_time": _contest_time(task.created_timestamp_seconds),
                "created_at_text": _created_at_text(task.created_at, ctx.contest.contest_timezone),
                "source_lines": source_lines,
            },
        ),
    )
