#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest-scoped chief-judge authority predicates shared by the web services.

The verdict (`judging_service`), task (`task_service`), and clarification
(`clarification_service`) permission modules all build on these two
predicates; keeping them here prevents the rule from drifting across domains.
"""

from __future__ import annotations

from shared.enumerations import RoleEnum
from web.models.contest import Contest
from web.models.users import UberAdmin, User


def is_chief_judge(actor: User | UberAdmin, contest: Contest) -> bool:
    """Return whether the actor is the chief judge of this contest.

    Only a JUDGE-role user can hold the designation, and uberadmins live in
    their own table, so neither can match `contests.chief_judge_id`, a foreign
    key into `users`.

    Args:
        actor: Authenticated actor to test.
        contest: Contest whose `chief_judge_id` designates the chief judge.

    Returns:
        `True` when the actor is the contest judge currently assigned as chief
        judge, `False` otherwise.
    """
    if isinstance(actor, UberAdmin) or actor.role != RoleEnum.JUDGE:
        return False
    return contest.chief_judge_id is not None and actor.id == contest.chief_judge_id


def has_chief_authority(actor: User | UberAdmin, contest: Contest) -> bool:
    """Return whether the actor carries chief authority in this contest.

    Contest admins and the contest's chief judge share the strongest
    contest-scoped authority: decisive verdict confirmations, verdict
    overrides, and lifecycle-independent announcement publishing.

    Args:
        actor: Authenticated actor to test.
        contest: Contest the authority would be exercised in.

    Returns:
        `True` for contest admins and the contest's chief judge, `False`
        otherwise (uberadmins included).
    """
    if isinstance(actor, UberAdmin):
        return False
    return actor.role == RoleEnum.ADMIN or is_chief_judge(actor, contest)
