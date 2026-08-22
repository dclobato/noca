#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Who may work on contest tasks."""

from __future__ import annotations

from shared.enumerations import RoleEnum
from web.models.contest import Contest
from web.models.users import UberAdmin, User
from web.services.chief_judge_permissions import is_chief_judge

__all__ = [
    "can_force_release_tasks",
    "can_handle_tasks",
    "can_view_tasks",
    "is_chief_judge",
]


def can_handle_tasks(actor: User | UberAdmin, contest: Contest) -> bool:
    """Return whether the actor may acquire and finish tasks.

    Uberadmins are excluded: a finished task is attributed through `tasks.staff_id`,
    which is a foreign key into `users`, and uberadmins live in their own table.
    """
    if isinstance(actor, UberAdmin):
        return False
    if actor.role in (RoleEnum.STAFF, RoleEnum.ADMIN):
        return True
    return is_chief_judge(actor, contest)


def can_force_release_tasks(actor: User | UberAdmin) -> bool:
    """Return whether the actor may take a task lock away from its holder."""
    return isinstance(actor, UberAdmin) or actor.role in (RoleEnum.ADMIN, RoleEnum.UBERADMIN)


def can_view_tasks(actor: User | UberAdmin, contest: Contest) -> bool:
    """Return whether the actor may open the tasks page at all.

    Judges see nothing here unless they are the contest's chief judge.
    """
    if isinstance(actor, UberAdmin):
        return True
    if actor.role == RoleEnum.JUDGE:
        return is_chief_judge(actor, contest)
    return actor.role in (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.STAFF, RoleEnum.TEAM)
