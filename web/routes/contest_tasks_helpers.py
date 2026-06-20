#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared helpers for contest task route modules."""

from __future__ import annotations

import datetime
from collections.abc import Sequence
from typing import cast

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from shared.enumerations import RoleEnum
from web.dependencies import ContestContext
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.users import UberAdmin, User
from web.routes.contest_admin_problem_helpers import _label
from web.services.assorted_utils import format_site_identity
from web.services.task_service import TaskView, list_tasks

_ALLOWED = (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.STAFF, RoleEnum.TEAM)
_STAFF_ONLY = (RoleEnum.STAFF,)
_RELEASE_ALLOWED = (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.STAFF)
_SOURCE_ALLOWED = (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.STAFF)


def _access_blocked(actor: UberAdmin | User, contest: Contest) -> bool:
    """ADMIN and UBERADMIN always have access; STAFF and TEAM only after the contest starts."""
    if isinstance(actor, UberAdmin) or actor.role in (RoleEnum.ADMIN,):
        return False
    return not (contest.is_running or contest.is_past)


def _html(response: object) -> HTMLResponse:
    return cast(HTMLResponse, response)


def _elapsed_str(delta: datetime.timedelta) -> str:
    total_s = max(0, int(delta.total_seconds()))
    mins, secs = divmod(total_s, 60)
    return f"{mins}m {secs}s"


def _queue_minutes_str(delta: datetime.timedelta) -> str:
    total_s = max(0, int(delta.total_seconds()))
    return f"{total_s // 60}m"


def _compute_queue_time_map(
    tasks: Sequence[TaskView],
    now_aware: datetime.datetime,
    now_naive: datetime.datetime,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for t in tasks:
        start = t.created_at
        if start.tzinfo is None:
            end = (
                t.finished_at.replace(tzinfo=None)
                if (t.finished_at and t.finished_at.tzinfo is not None)
                else (t.finished_at or now_naive)
            )
        else:
            end = t.finished_at if t.finished_at else now_aware
        result[t.id] = _queue_minutes_str(end - start)
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
    now_naive = datetime.datetime.utcnow()

    if role == RoleEnum.TEAM:
        problem_map: dict[str, str] = {p.id: f"{_label(p.ordinal)}: {p.title}" for p in problems}
        problem_color_map: dict[str, str] = {p.id: p.color for p in problems}
        return {
            "tasks": tasks,
            "lock_service_available": lock_service_available,
            "problems": problems,
            "problem_map": problem_map,
            "problem_color_map": problem_color_map,
            "queue_time_map": _compute_queue_time_map(tasks, now_aware, now_naive),
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
        "team_map": team_map,
        "staff_map": staff_map,
        "team_location_map": team_location_map,
        "problem_map": rich_problem_map,
        "problem_color_map": rich_problem_color_map,
        "queue_time_map": _compute_queue_time_map(tasks, now_aware, now_naive),
    }
    if role in (RoleEnum.ADMIN,) or isinstance(ctx.actor, UberAdmin):
        service_time_map: dict[str, str | None] = {}
        for t in tasks:
            if t.acquired_at is None:
                service_time_map[t.id] = None
                continue
            acq = t.acquired_at
            if acq.tzinfo is None:
                end = (
                    t.finished_at.replace(tzinfo=None)
                    if (t.finished_at and t.finished_at.tzinfo is not None)
                    else (t.finished_at or now_naive)
                )
                delta = end - acq
            else:
                end = t.finished_at if t.finished_at else now_aware
                delta = end - acq
            service_time_map[t.id] = _elapsed_str(delta)
        ctx_data["service_time_map"] = service_time_map

    return ctx_data
