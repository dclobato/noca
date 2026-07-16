#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Who may confirm and override submission verdicts."""

from __future__ import annotations

from shared.enumerations import RoleEnum
from web.models import Contest, UberAdmin, User


def is_chief_judge(actor: User | UberAdmin, contest: Contest) -> bool:
    """Return whether the actor is the chief judge of this contest."""
    if isinstance(actor, UberAdmin) or actor.role != RoleEnum.JUDGE:
        return False
    return contest.chief_judge_id is not None and actor.id == contest.chief_judge_id


def can_confirm_verdict(actor: User | UberAdmin, contest: Contest) -> bool:
    """Return whether the actor may acquire a review and confirm a verdict.

    Uberadmins are excluded: a confirmation is attributed through
    `human_submission_confirmations.judge_id`, a foreign key into `users`, and
    uberadmins live in their own table.
    """
    if isinstance(actor, UberAdmin):
        return False
    return actor.role in (RoleEnum.JUDGE, RoleEnum.ADMIN)


def confirmation_is_decisive(actor: User | UberAdmin, contest: Contest) -> bool:
    """Return whether this actor's confirmation settles the final verdict on its own.

    The chief judge and contest admins both carry chief authority. A judgment may
    hold at most one such confirmation (`_derive_final_verdict` enforces it), so
    `confirm_verdict` refuses a second one.
    """
    if isinstance(actor, UberAdmin):
        return False
    return actor.role == RoleEnum.ADMIN or is_chief_judge(actor, contest)


def can_override_verdict(actor: User | UberAdmin, contest: Contest) -> bool:
    """Return whether the actor may override an existing final verdict.

    Uberadmins are excluded: `verdict_overrides.overridden_by` is a foreign key
    into `users`.
    """
    if isinstance(actor, UberAdmin):
        return False
    return actor.role == RoleEnum.ADMIN or is_chief_judge(actor, contest)
