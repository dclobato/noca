#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import datetime
from collections.abc import Sequence
from typing import cast

from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.enumerations import RoleEnum
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.users import UberAdmin, User
from web.routes.contest_admin_problem_helpers import _label
from web.services.assorted_utils import format_site_identity
from web.services.clarification_service import ClarificationView
from web.services.time_utils import elapsed_since, format_elapsed_minutes

_ALLOWED = (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE, RoleEnum.TEAM)
_JUDGE_ONLY = (RoleEnum.JUDGE,)
_ANSWER_ALLOWED = (RoleEnum.JUDGE, RoleEnum.ADMIN)
_ADMIN_ONLY = (RoleEnum.UBERADMIN, RoleEnum.ADMIN)


def _html(response: object) -> HTMLResponse:
    return cast(HTMLResponse, response)


def _problem_map_from_list(problems: list[Problem]) -> dict[str, str]:
    return {p.id: f"{_label(p.ordinal)}: {p.title}" for p in problems}


async def _build_problem_map(session: AsyncSession, contest: Contest) -> dict[str, str]:
    result = await session.execute(select(Problem).where(Problem.contest_id == contest.id).order_by(Problem.ordinal))
    return _problem_map_from_list(list(result.scalars().all()))


async def _build_user_map(session: AsyncSession, contest: Contest) -> dict[str, str]:
    result = await session.execute(select(User).where(User.contest_id == contest.id).options(selectinload(User.site)))
    return {
        u.id: format_site_identity(
            u.site.sitename if u.site is not None else None,
            u.fullname or u.username,
        )
        for u in result.scalars().all()
    }


def _needs_user_map(actor: UberAdmin | User) -> bool:
    return isinstance(actor, UberAdmin) or actor.role == RoleEnum.ADMIN


def _elapsed_str(delta: datetime.timedelta) -> str:
    total_s = max(0, int(delta.total_seconds()))
    mins, secs = divmod(total_s, 60)
    return f"{mins}m {secs}s"


def _compute_queue_time_map(
    clarifications: Sequence[ClarificationView],
    now_aware: datetime.datetime,
) -> dict[str, str | None]:
    """Map queued questions to their total open time in whole minutes."""
    result: dict[str, str | None] = {}
    for clarification in clarifications:
        if clarification.is_announcement or clarification.hidden:
            result[clarification.id] = None
            continue
        end = clarification.answered_at or now_aware
        result[clarification.id] = format_elapsed_minutes(clarification.created_at, now=end)
    return result


def _compute_service_time_map(
    clarifications: Sequence[ClarificationView],
    now_aware: datetime.datetime,
) -> dict[str, str | None]:
    """Map each clarification to its service time, or ``None`` for a "--" cell.

    Mirrors ``contest_tasks_helpers._compute_service_time_map``: an answered
    clarification reads its service time from ``service_started_at``, the
    persisted acquisition instant, never from the live lock -- the lock is
    already released by the time a clarification is answered. An unanswered
    one instead reads the live lock (``acquired_at``) and ticks against "now",
    so a clarification whose lock expired and was dropped never shows a
    growing service time for nobody.
    """
    result: dict[str, str | None] = {}
    for c in clarifications:
        if c.answered_at is not None and c.service_started_at is not None:
            result[c.id] = _elapsed_str(elapsed_since(c.service_started_at, now=c.answered_at))
        elif c.answered_at is None and c.acquired_at is not None:
            result[c.id] = _elapsed_str(elapsed_since(c.acquired_at, now=now_aware))
        else:
            result[c.id] = None
    return result
