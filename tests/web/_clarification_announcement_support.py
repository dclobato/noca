#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared row builders for the announcement-notification test modules."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from web.models.clarification import Clarification, ClarificationRead
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.users import UberAdmin, User
from web.services.clarification_service import create_announcement


async def publish_announcement(
    session: AsyncSession,
    contest: Contest,
    actor: User,
    *,
    text: str = "The judges will restart problem A.",
) -> Clarification:
    """Publish an announcement through the real service path."""
    announcement = await create_announcement(
        session,
        contest,
        actor,
        problem_id=None,
        announcement=text,
    )
    await session.flush()
    return announcement


async def answered_question(
    session: AsyncSession,
    *,
    team: User,
    problem: Problem,
    read: bool = False,
    is_contest_public: bool = False,
) -> Clarification:
    """Create an answered clarification asked by *team*."""
    now = datetime.now(UTC)
    clarification = Clarification(
        team_id=team.id,
        problem_id=problem.id,
        question="What is the limit?",
        answer="The limit is 100.",
        is_contest_public=is_contest_public,
        answered_at=now,
        answered_timestamp_seconds=60,
        answer_read_at=now if read else None,
        created_at=now,
        created_timestamp_seconds=30,
    )
    session.add(clarification)
    await session.flush()
    return clarification


async def add_user(
    session: AsyncSession,
    contest: Contest,
    uberadmin: UberAdmin,
    username: str,
    role: RoleEnum,
) -> User:
    """Create one contest user in the given role."""
    user = User(
        username=username,
        fullname=username.replace("_", " ").title(),
        role=role,
        contest_id=contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


async def read_row_count(session: AsyncSession, clarification_id: str, user_id: str) -> int:
    """Count read markers for one (announcement, team) pair."""
    result = await session.execute(
        select(func.count())
        .select_from(ClarificationRead)
        .where(
            ClarificationRead.clarification_id == clarification_id,
            ClarificationRead.user_id == user_id,
        )
    )
    return int(result.scalar() or 0)
