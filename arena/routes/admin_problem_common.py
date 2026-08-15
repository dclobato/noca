#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Lookups shared by the Arena admin problem route modules.

These live apart from ``admin_problems`` so the validator, test-case, and I/O
route modules can reuse them without importing each other.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import ArenaProblem
from arena.models.arena_users import ArenaUser
from arena.services import admin_problem_service
from shared.db_schema import languages as languages_table
from shared.enumerations import ArenaRole


async def get_problem_or_403(
    problem_id: str,
    current_user: ArenaUser,
    session: AsyncSession,
) -> ArenaProblem:
    """Fetch a problem with ownership check for ARENA_JUDGE users."""
    is_admin_user = current_user.role == ArenaRole.ARENA_ADMIN
    problem = await admin_problem_service.get_problem(
        session, problem_id, caller_id=current_user.id, is_admin=is_admin_user
    )
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem not found")
    return problem


async def get_problem_definition_or_403(
    problem_id: str,
    current_user: ArenaUser,
    session: AsyncSession,
) -> ArenaProblem:
    """Fetch the narrow definition-editor view with ownership enforcement."""
    is_admin_user = current_user.role == ArenaRole.ARENA_ADMIN
    problem = await admin_problem_service.get_problem_definition(
        session,
        problem_id,
        caller_id=current_user.id,
        is_admin=is_admin_user,
    )
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem not found")
    return problem


async def validator_languages(session: AsyncSession) -> list[Any]:
    """Return globally active Autojudge languages for validator forms."""
    result = await session.execute(
        select(languages_table.c.id, languages_table.c.name, languages_table.c.source_filename)
        .where(languages_table.c.active.is_(True))
        .order_by(languages_table.c.name)
    )
    return list(result.all())
