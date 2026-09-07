#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The administrator's side of the single-session policy.

`web.services.session_policy` owns what the policy *does*; this module owns the
two things an organiser does to it -- turning it on or off for a whole contest,
and reading back which teams are currently bound so the enrolled-users page can
show it. Releasing one team's binding is not here: that is
`session_policy.clear_ip_lock`, because it is a state transition of the policy
rather than an administrative preference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import users as users_t
from shared.enumerations import RoleEnum
from web.models.contest import Contest


@dataclass(frozen=True, slots=True)
class SessionPolicyChange:
    """What one application of the contest-wide policy actually did.

    Attributes:
        teams_changed: Teams whose flag the statement moved. Rows already
            holding the requested value are excluded, so a repeated click
            reports ``0`` rather than claiming the whole contest again.
        bindings_released: Teams whose IP binding was cleared. Only a *lift*
            releases anything; applying the policy releases nothing.
    """

    teams_changed: int
    bindings_released: int


async def set_contest_team_session_policy(
    session: AsyncSession,
    contest: Contest,
    *,
    allow_concurrent_login: bool,
) -> SessionPolicyChange:
    """Apply one session policy to every team of a contest.

    Args:
        session: Session owning the caller's transaction. Not committed here, so
            the change lands with the audit row that records who ordered it.
        contest: The contest whose teams are being set.
        allow_concurrent_login: The policy to apply. ``True`` lifts it.

    Returns:
        What the change did, as a :class:`SessionPolicyChange`.

    Notes:
        **Teams only.** Staff are exempt by role, so setting their flag would
        change nothing while making the enrolled page imply otherwise -- and
        `policy_may_apply` would still let them in, which is exactly the
        behaviour that must not look like a bug to whoever reads the row next.

        **Lifting the policy releases the bindings it made.** Keeping them was
        the original decision -- the addresses are inert the moment the flag is
        set, and they record where each team sat -- but that record turned the
        round trip into a trap. Re-applying the policy would enforce addresses
        captured before the lift, so every team that had moved was refused at
        exactly the moment an organiser was trying to let them back in, which is
        the likeliest reason to lift the rule at all. A lift now means what it
        says: the rule is off and the addresses it recorded are gone, so
        re-applying it binds each team wherever it is then.

        The release does **not** bump `session_epoch`, for the same reason the
        end-of-contest reaper does not: the policy has stopped applying, so
        there is no session left to supersede, and bumping would sign out a
        whole contest to no purpose. It is the opposite of `clear_ip_lock`,
        which releases one team *while the rule still governs it* and must
        therefore supersede the sessions bound to the old address.
    """
    statement = (
        update(users_t)
        .where(
            users_t.c.contest_id == contest.id,
            users_t.c.role == RoleEnum.TEAM,
            users_t.c.allow_concurrent_login.is_(not allow_concurrent_login),
        )
        .values(allow_concurrent_login=allow_concurrent_login)
    )
    result = cast(CursorResult[Any], await session.execute(statement))
    teams_changed = int(result.rowcount or 0)

    bindings_released = 0
    if allow_concurrent_login:
        # Every bound team of the contest, not only the rows whose flag just
        # moved: a team released individually earlier can still be carrying an
        # address, and leaving that one behind would rebuild the trap for it.
        release = (
            update(users_t)
            .where(
                users_t.c.contest_id == contest.id,
                users_t.c.role == RoleEnum.TEAM,
                users_t.c.locked_ip.is_not(None),
            )
            .values(locked_ip=None, locked_at=None)
        )
        released = cast(CursorResult[Any], await session.execute(release))
        bindings_released = int(released.rowcount or 0)

    return SessionPolicyChange(teams_changed=teams_changed, bindings_released=bindings_released)


async def count_bound_teams(session: AsyncSession, contest: Contest) -> int:
    """Return how many of a contest's teams currently hold an IP binding.

    Args:
        session: Database session.
        contest: The contest to count within.

    Returns:
        The number of teams with a `locked_ip`, whatever their flag says -- a
        binding made before the flag was turned off is still a binding, and the
        page offers its release either way.
    """
    statement = (
        select(func.count())
        .select_from(users_t)
        .where(
            users_t.c.contest_id == contest.id,
            users_t.c.role == RoleEnum.TEAM,
            users_t.c.locked_ip.is_not(None),
        )
    )
    return int(await session.scalar(statement) or 0)


async def count_restricted_teams(session: AsyncSession, contest: Contest) -> int:
    """Return how many of a contest's teams the policy governs.

    Args:
        session: Database session.
        contest: The contest to count within.

    Returns:
        The number of teams whose `allow_concurrent_login` is cleared. The
        enrolled page uses it to say which way the contest-wide control is
        currently set, including the mixed state a per-user edit can create.
    """
    statement = (
        select(func.count())
        .select_from(users_t)
        .where(
            users_t.c.contest_id == contest.id,
            users_t.c.role == RoleEnum.TEAM,
            users_t.c.allow_concurrent_login.is_(False),
        )
    )
    return int(await session.scalar(statement) or 0)
