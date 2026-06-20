"""Chief judge management helpers."""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from typing import NamedTuple

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.enumerations import RoleEnum
from web.models import Contest, Problem, Submission, User, VerdictOverride
from web.models.users import UberAdmin
from web.services.contest_service import validate_chief_judge_assignment


class ChiefJudgeAdminPanel(NamedTuple):
    """Chief-judge panel data for the admin UI."""

    current_chief_judge: User | None
    judges: list[User]
    can_remove: bool


async def list_contest_judges(session: AsyncSession, contest: Contest) -> list[User]:
    """Return all judges in the contest."""
    result = await session.execute(
        select(User)
        .where(User.contest_id == contest.id, User.role == RoleEnum.JUDGE)
        .order_by(User.fullname, User.username)
        .options(selectinload(User.site))
    )
    return list(result.scalars().all())


async def get_chief_judge_admin_panel(session: AsyncSession, contest: Contest) -> ChiefJudgeAdminPanel:
    """Build the chief-judge admin panel model."""
    judges = await list_contest_judges(session, contest)
    current_chief_judge = next((judge for judge in judges if judge.id == contest.chief_judge_id), None)
    if current_chief_judge is None and contest.chief_judge_id is not None:
        current_chief_judge = (
            await session.execute(
                select(User)
                .where(User.id == contest.chief_judge_id, User.contest_id == contest.id)
                .options(selectinload(User.site))
            )
        ).scalar_one_or_none()

    can_remove = False
    if current_chief_judge is not None:
        has_override = (
            await session.execute(
                select(VerdictOverride.id)
                .join(Submission, VerdictOverride.submission_id == Submission.id)
                .join(Problem, Submission.problem_id == Problem.id)
                .where(
                    VerdictOverride.overridden_by == current_chief_judge.id,
                    Problem.contest_id == contest.id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        can_remove = has_override is None

    return ChiefJudgeAdminPanel(
        current_chief_judge=current_chief_judge,
        judges=judges,
        can_remove=can_remove,
    )


async def set_chief_judge(
    session: AsyncSession,
    contest: Contest,
    judge_id: str | None,
    requesting_user: UberAdmin | User,
) -> Contest:
    """Assign or clear the contest chief judge."""
    if not isinstance(requesting_user, UberAdmin) and requesting_user.id != contest.owner_user_id:
        raise HTTPException(status_code=403)
    if judge_id is None or not judge_id.strip():
        contest.chief_judge_id = None
        await session.flush()
        return contest

    errors = await validate_chief_judge_assignment(session, contest, judge_id)
    if errors:
        raise HTTPException(status_code=400, detail=errors[0])

    contest.chief_judge_id = judge_id
    await session.flush()
    return contest


async def remove_chief_judge(
    session: AsyncSession,
    contest: Contest,
    requesting_user: UberAdmin | User,
) -> Contest:
    """Clear the contest chief judge if removal is allowed."""
    from .errors import ChiefJudgeRemovalBlockedError

    if not isinstance(requesting_user, UberAdmin) and requesting_user.id != contest.owner_user_id:
        raise HTTPException(status_code=403)
    if contest.chief_judge_id is None:
        await session.flush()
        return contest

    has_override = (
        await session.execute(
            select(VerdictOverride.id)
            .join(Submission, VerdictOverride.submission_id == Submission.id)
            .join(Problem, Submission.problem_id == Problem.id)
            .where(
                VerdictOverride.overridden_by == contest.chief_judge_id,
                Problem.contest_id == contest.id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if has_override is not None:
        raise ChiefJudgeRemovalBlockedError

    contest.chief_judge_id = None
    await session.flush()
    return contest
