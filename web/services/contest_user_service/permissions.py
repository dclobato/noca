#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Authorization and contest-state guards for contest users."""

from __future__ import annotations

from fastapi import HTTPException

from shared.enumerations import RoleEnum
from web.models.contest import Contest
from web.models.users import UberAdmin, User


def ensure_contest_user_add_or_edit_allowed(contest: Contest) -> None:
    """Ensure the contest still allows user creation or editing."""
    if contest.is_past:
        raise HTTPException(
            status_code=403,
            detail="Cannot add or edit users after the contest has finished.",
        )


def ensure_contest_user_remove_allowed(contest: Contest) -> None:
    """Ensure the contest still allows user removal."""
    if contest.is_running or contest.is_past:
        raise HTTPException(
            status_code=403,
            detail="Cannot remove users while contest is running or finished.",
        )


def ensure_user_edit_allowed(actor: User | UberAdmin, target_user: User) -> None:
    """Ensure the actor may edit the target contest user."""
    if isinstance(actor, User) and actor.id == target_user.id:
        raise HTTPException(
            status_code=403,
            detail="You cannot edit your own contest user record.",
        )


def ensure_user_photo_upload_allowed(actor: User | UberAdmin, target_user: User) -> None:
    """Ensure the actor may upload a replacement photo for the target user."""
    if isinstance(actor, User) and actor.id == target_user.id:
        return
    raise HTTPException(
        status_code=403,
        detail="You can only upload your own photo.",
    )


def ensure_user_photo_removal_allowed(actor: User | UberAdmin, target_user: User) -> None:
    """Ensure the actor may remove the target user's stored photo."""
    if isinstance(actor, UberAdmin):
        return
    if actor.id == target_user.id:
        return
    if actor.role == RoleEnum.ADMIN:
        return
    raise HTTPException(
        status_code=403,
        detail="Only contest admins can remove another user's photo.",
    )
