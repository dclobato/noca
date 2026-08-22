#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Who may answer contest clarifications and publish announcements.

Chief-judge and chief-authority predicates live in
`web/services/chief_judge_permissions.py`, shared with the verdict rules in
`judging_service/permissions.py` and the task rules in
`task_service/permissions.py`.
"""

from __future__ import annotations

from shared.enumerations import RoleEnum
from web.models.contest import Contest
from web.models.users import UberAdmin, User
from web.services.chief_judge_permissions import has_chief_authority


def can_answer_clarifications(actor: User | UberAdmin) -> bool:
    """Return whether the actor may acquire and answer clarifications.

    Uberadmins are excluded: an answered clarification is attributed through
    `clarifications.judge_id`, a foreign key into `users`, and uberadmins live in
    their own table.
    """
    if isinstance(actor, UberAdmin):
        return False
    return actor.role in (RoleEnum.JUDGE, RoleEnum.ADMIN)


def can_request_clarification(actor: User | UberAdmin, contest: Contest) -> bool:
    """Return whether the actor may request a clarification right now.

    Args:
        actor: Authenticated actor attempting to submit a question.
        contest: Contest in which the question would be submitted.

    Returns:
        `True` for a team while the contest is running.
    """
    return isinstance(actor, User) and actor.role == RoleEnum.TEAM and contest.is_running


def can_force_release_clarifications(actor: User | UberAdmin) -> bool:
    """Return whether the actor may take a clarification lock away from its holder."""
    return isinstance(actor, UberAdmin) or actor.role in (RoleEnum.ADMIN, RoleEnum.UBERADMIN)


def can_create_announcement(actor: User | UberAdmin, contest: Contest) -> bool:
    """Return whether the actor may publish an announcement right now.

    Admins and judges may publish while the contest is running. Contest admins
    and the contest's chief judge (see `has_chief_authority`) may publish at
    any point in the contest lifecycle, so they can address participants before
    the start and after the end. Uberadmins are excluded for the same reason
    they cannot answer clarifications: authorship is recorded through
    `clarifications.judge_id`, a foreign key into `users`.

    Args:
        actor: Authenticated actor attempting to publish.
        contest: Contest the announcement would belong to.

    Returns:
        `True` when the actor may publish an announcement in the contest's
        current lifecycle state.
    """
    if isinstance(actor, UberAdmin) or actor.role not in (RoleEnum.ADMIN, RoleEnum.JUDGE):
        return False
    return contest.is_running or has_chief_authority(actor, contest)
