#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Who may answer contest clarifications."""

from __future__ import annotations

from shared.enumerations import RoleEnum
from web.models.users import UberAdmin, User


def can_answer_clarifications(actor: User | UberAdmin) -> bool:
    """Return whether the actor may acquire and answer clarifications.

    Uberadmins are excluded: an answered clarification is attributed through
    `clarifications.judge_id`, a foreign key into `users`, and uberadmins live in
    their own table.
    """
    if isinstance(actor, UberAdmin):
        return False
    return actor.role in (RoleEnum.JUDGE, RoleEnum.ADMIN)


def can_force_release_clarifications(actor: User | UberAdmin) -> bool:
    """Return whether the actor may take a clarification lock away from its holder."""
    return isinstance(actor, UberAdmin) or actor.role in (RoleEnum.ADMIN, RoleEnum.UBERADMIN)
