#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

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

_ALLOWED = (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE, RoleEnum.TEAM)
_JUDGE_ONLY = (RoleEnum.JUDGE,)
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


def _team_access_blocked(actor: UberAdmin | User, contest: Contest) -> bool:
    """Teams may only access clarifications once the contest has started."""
    if isinstance(actor, UberAdmin) or actor.role != RoleEnum.TEAM:
        return False
    return not (contest.is_running or contest.is_past)
