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


def clear_chief_judge_if_no_longer_judge(contest: Contest, user: User) -> None:
    """Clear chief_judge_id when the user is no longer a JUDGE."""
    if contest.chief_judge_id == user.id and user.role != RoleEnum.JUDGE:
        contest.chief_judge_id = None
