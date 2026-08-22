#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Who may confirm and override submission verdicts."""

from __future__ import annotations

from shared.enumerations import RoleEnum
from web.models import Contest, UberAdmin, User
from web.services.chief_judge_permissions import has_chief_authority, is_chief_judge

__all__ = [
    "can_confirm_verdict",
    "can_override_verdict",
    "can_supervise_judgment",
    "confirmation_is_decisive",
    "is_chief_judge",
]


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

    The chief judge and contest admins both carry chief authority (see
    `web/services/chief_judge_permissions.py`). A judgment may hold at most one
    such confirmation (`_derive_final_verdict` enforces it), so
    `confirm_verdict` refuses a second one.
    """
    return has_chief_authority(actor, contest)


def can_override_verdict(actor: User | UberAdmin, contest: Contest) -> bool:
    """Return whether the actor may override an existing final verdict.

    Uberadmins are excluded: `verdict_overrides.overridden_by` is a foreign key
    into `users`.
    """
    return has_chief_authority(actor, contest)


def can_supervise_judgment(actor: User | UberAdmin, contest: Contest) -> bool:
    """Return whether the actor may supervise another actor's judgment work.

    Supervision is rejudging a submission and force-releasing someone else's
    review lock. Unlike confirming or overriding, neither records who did it, so
    uberadmins *are* included here -- this is exactly the "supervise but not
    perform" line the attribution boundary draws (see
    `web/docs/ROUTES.md`, "The uberadmin attribution boundary").

    Args:
        actor: Authenticated actor to test.
        contest: Contest the supervision would be exercised in.

    Returns:
        `True` for uberadmins, contest admins, and the contest's chief judge.
    """
    return isinstance(actor, UberAdmin) or has_chief_authority(actor, contest)
