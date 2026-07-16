#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Authorization and chief-judge helpers for contest service operations."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from web.models.contest import Contest
from web.models.users import UberAdmin, User
from web.services.site_service import contest_has_sites


def ensure_contest_admin_or_uberadmin(actor: User | UberAdmin) -> None:
    """Ensure the actor has contest-admin level access."""
    if actor.role not in (RoleEnum.UBERADMIN, RoleEnum.ADMIN):
        raise HTTPException(status_code=403)


async def ensure_contest_has_sites(session: AsyncSession, contest: Contest) -> None:
    """Ensure the contest has at least one site configured."""
    if not await contest_has_sites(session, contest.id):
        raise ValueError("At least one site is required before starting or editing this contest.")


async def validate_chief_judge_assignment(
    session: AsyncSession,
    contest: Contest,
    user_id: str,
) -> list[str]:
    """Validate whether a user can be assigned as the contest chief judge."""
    result = await session.execute(select(User).where(User.id == user_id, User.contest_id == contest.id))
    user = result.scalar_one_or_none()
    errors: list[str] = []
    if user is None:
        errors.append("O usuário selecionado não é membro deste contest.")
    elif user.role != RoleEnum.JUDGE:
        errors.append("Apenas usuários com role JUDGE podem ser designados como chief judge.")
    return errors


class ChiefJudgeInvariantError(ValueError):
    """Raised when an operation would leave a judged contest without a chief judge."""


async def list_contest_judge_ids(
    session: AsyncSession,
    contest: Contest,
    *,
    exclude_user_id: str | None = None,
) -> list[str]:
    """Return the ids of the contest judges, ordered deterministically."""
    query = select(User.id).where(User.contest_id == contest.id, User.role == RoleEnum.JUDGE)
    if exclude_user_id is not None:
        query = query.where(User.id != exclude_user_id)
    query = query.order_by(User.fullname, User.username)
    return list((await session.execute(query)).scalars().all())


async def ensure_chief_judge_reassignable(session: AsyncSession, contest: Contest, user: User) -> None:
    """Ensure the chief judge may stop being a judge without breaking the invariant.

    A contest with judges must have a chief judge. Dropping the current chief judge is only
    safe when the successor is unambiguous: no remaining judge (the field is cleared) or
    exactly one (it is promoted). With two or more remaining judges the owner must hand the
    role over first.
    """
    if contest.chief_judge_id != user.id:
        return
    remaining = await list_contest_judge_ids(session, contest, exclude_user_id=user.id)
    if len(remaining) >= 2:
        raise ChiefJudgeInvariantError(
            "Assign a different chief judge before changing or removing this user: "
            "the contest has other judges and no unambiguous successor."
        )


async def reconcile_chief_judge(
    session: AsyncSession,
    contest: Contest,
    *,
    excluded_user_id: str | None = None,
) -> None:
    """Restore the chief-judge invariant after a role change or user removal.

    Keeps a still-valid chief judge, promotes the only judge of the contest, and clears the
    assignment when no judge is left. A contest with several judges and no valid chief judge
    is legacy data with no unambiguous successor, so it is left untouched.
    """
    judge_ids = await list_contest_judge_ids(session, contest, exclude_user_id=excluded_user_id)
    if contest.chief_judge_id in judge_ids:
        return
    if len(judge_ids) == 1:
        contest.chief_judge_id = judge_ids[0]
    elif not judge_ids:
        contest.chief_judge_id = None
