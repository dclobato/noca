#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Read-side helpers for contest service operations."""

from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.db_schema import contest_languages as contest_languages_table
from web.database import get_db
from web.models.contest import Contest
from web.models.language import Language


async def get_contest_by_slug(slug: str, session: AsyncSession = Depends(get_db)) -> Contest:
    """Load an active contest by login slug."""
    stmt = select(Contest).options(selectinload(Contest.allowed_languages)).where(Contest.login_slug == slug)
    contest = (await session.execute(stmt)).scalar_one_or_none()
    if not contest or not contest.active:
        raise HTTPException(status_code=404)
    return contest


async def get_contest_by_id(session: AsyncSession, contest_id: str) -> Contest | None:
    """Load a contest by primary key."""
    return (await session.execute(select(Contest).where(Contest.id == contest_id))).scalar_one_or_none()


async def get_inactive_contests(session: AsyncSession) -> list[Contest]:
    """Return inactive contests ordered by most recent end/start time."""
    stmt = (
        select(Contest).options(selectinload(Contest.allowed_languages)).where(Contest.active == False)  # noqa: E712
    )
    contests = list((await session.execute(stmt)).scalars().all())
    return sorted(
        contests,
        key=lambda contest: (contest.end_time, contest.start_time, contest.login_slug),
        reverse=True,
    )


async def get_contest_language_ids(session: AsyncSession, contest: Contest) -> list[str]:
    """Return allowed language IDs for a contest ordered by language name."""
    result = await session.execute(
        select(Language.id)
        .join(contest_languages_table, Language.id == contest_languages_table.c.language_id)
        .where(contest_languages_table.c.contest_id == contest.id)
        .order_by(Language.name)
    )
    return list(result.scalars().all())


async def get_active_languages(session: AsyncSession) -> list[Language]:
    """Return all active languages ordered by name."""
    result = await session.execute(
        select(Language).where(Language.active == True).order_by(Language.name)  # noqa: E712
    )
    return list(result.scalars().all())
