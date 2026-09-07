#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared helpers for contest task route modules."""

from __future__ import annotations

import datetime
from collections.abc import Sequence
from typing import cast

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from shared.enumerations import RoleEnum
from web.dependencies import ContestContext, ensure_allowed_role
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.users import UberAdmin, User
from web.routes.contest_admin_problem_helpers import _label
from web.services.assorted_utils import format_site_identity
from web.services.task_service import (
    TaskView,
    can_force_release_tasks,
    can_handle_tasks,
    can_view_tasks,
    list_tasks,
)
from web.services.time_utils import elapsed_since, format_elapsed_minutes

_ALLOWED = (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE, RoleEnum.STAFF, RoleEnum.TEAM)
_HANDLE_ALLOWED = (RoleEnum.ADMIN, RoleEnum.JUDGE, RoleEnum.STAFF)
_RELEASE_ALLOWED = (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE, RoleEnum.STAFF)
_SOURCE_ALLOWED = (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE, RoleEnum.STAFF)


def _ensure_task_access(actor: UberAdmin | User, contest: Contest) -> None:
    """Raise `403` unless the actor may work with this contest's tasks.

    `_ALLOWED` lets the JUDGE role through so the chief judge can reach the page;
    every other judge is rejected here.
    """
    ensure_allowed_role(actor, _ALLOWED)
    if not can_view_tasks(actor, contest):
        raise HTTPException(status_code=403)


def _access_blocked(actor: UberAdmin | User, contest: Contest) -> bool:
    """ADMIN and UBERADMIN always have access; the others only after the contest starts."""
    if isinstance(actor, UberAdmin) or actor.role in (RoleEnum.ADMIN,):
        return False
    return not (contest.is_running or contest.is_past)


def _html(response: object) -> HTMLResponse:
    return cast(HTMLResponse, response)


def _elapsed_str(delta: datetime.timedelta) -> str:
    total_s = max(0, int(delta.total_seconds()))
    mins, secs = divmod(total_s, 60)
    return f"{mins}m {secs}s"


def _compute_queue_time_map(
    tasks: Sequence[TaskView],
    now_aware: datetime.datetime,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for t in tasks:
        result[t.id] = format_elapsed_minutes(t.created_at, now=t.finished_at or now_aware)
    return result


def _compute_service_time_map(
    tasks: Sequence[TaskView],
    now_aware: datetime.datetime,
) -> dict[str, str | None]:
    """Map each task to its service time, or ``None`` for a "--" cell.

    A finished task's service time is read from ``service_started_at``, the
    persisted acquisition instant, never from the live lock -- the lock is
    already released by the time a task finishes. An in-progress task instead
    reads the live lock (``acquired_at``) and ticks against "now", exactly as
    before this persisted column existed: reading the persisted column there
    too would keep counting up for a task whose lock expired and was dropped,
    showing a growing service time for a task nobody is handling.
    """
    result: dict[str, str | None] = {}
    for t in tasks:
        if t.finished_at is not None and t.service_started_at is not None:
            result[t.id] = _elapsed_str(elapsed_since(t.service_started_at, now=t.finished_at))
        elif t.finished_at is None and t.acquired_at is not None:
            result[t.id] = _elapsed_str(elapsed_since(t.acquired_at, now=now_aware))
        else:
            result[t.id] = None
    return result


async def _build_template_context(ctx: ContestContext, request: Request) -> dict[str, object]:
    tasks_raw, lock_service_available = await list_tasks(
        ctx.session,
        ctx.contest,
        ctx.actor,
        request.app.state.valkey_runtime,
    )
    tasks = list(reversed(tasks_raw))

    result = await ctx.session.execute(
        select(Problem).where(Problem.contest_id == ctx.contest.id).order_by(Problem.ordinal)
    )
    problems = list(result.scalars().all())

    role = ctx.actor.role if isinstance(ctx.actor, User) else None
    now_aware = datetime.datetime.now(datetime.UTC)

    if role == RoleEnum.TEAM:
        problem_map: dict[str, str] = {p.id: f"{_label(p.ordinal)}: {p.title}" for p in problems}
        problem_color_map: dict[str, str] = {p.id: p.color for p in problems}
        return {
            "tasks": tasks,
            "lock_service_available": lock_service_available,
            "can_handle_tasks": False,
            "can_force_release": False,
            "problems": problems,
            "problem_map": problem_map,
            "problem_color_map": problem_color_map,
            "queue_time_map": _compute_queue_time_map(tasks, now_aware),
        }

    users_result = await ctx.session.execute(
        select(User).where(User.contest_id == ctx.contest.id).options(selectinload(User.site))
    )
    all_users = list(users_result.scalars().all())
    team_map: dict[str, str] = {
        u.id: format_site_identity(
            u.site.sitename if u.site is not None else None,
            u.fullname or u.username,
        )
        for u in all_users
    }
    staff_map: dict[str, str] = {
        u.id: format_site_identity(
            u.site.sitename if u.site is not None else None,
            u.username,
        )
        for u in all_users
    }
    team_location_map: dict[str, str] = {u.id: (u.location or "") for u in all_users}
    rich_problem_map: dict[str, dict[str, str]] = {
        p.id: {"label": _label(p.ordinal), "title": p.title, "color": p.color} for p in problems
    }
    rich_problem_color_map: dict[str, str] = {p.id: p.color for p in problems}

    ctx_data: dict[str, object] = {
        "tasks": tasks,
        "lock_service_available": lock_service_available,
        "can_handle_tasks": can_handle_tasks(ctx.actor, ctx.contest),
        "can_force_release": can_force_release_tasks(ctx.actor),
        "team_map": team_map,
        "staff_map": staff_map,
        "team_location_map": team_location_map,
        "problem_map": rich_problem_map,
        "problem_color_map": rich_problem_color_map,
        "queue_time_map": _compute_queue_time_map(tasks, now_aware),
    }
    if role in (RoleEnum.ADMIN,) or isinstance(ctx.actor, UberAdmin):
        ctx_data["service_time_map"] = _compute_service_time_map(tasks, now_aware)

    return ctx_data
