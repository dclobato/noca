#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The dynamic ROCK_CRACKER badge rule.

ROCK_CRACKER follows the current participant-only solve rate rather than the
batch-derived problem-rating cache. Incremental passes award from the complete
current population of affected problems, while full passes reconcile the
complete holder set and revoke stale rows.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Subquery

from shared.db_schema.arena import arena_problem_solvers, arena_problems
from shared.db_schema.arena import arena_submissions as _submissions
from shared.db_schema.arena import arena_user_badges as _user_badges
from shared.enumerations import ArenaBadge
from shared.services.arena_badge_data import award_badge, load_first_ac_submissions, revoke_badge_except
from shared.services.arena_query_helpers import counts_toward_problem_rating

_ROCK_CRACKER_RATE_DENOMINATOR = 5


def _attempted_users_by_problem(problem_ids: set[str] | None) -> Subquery:
    """Return participant attempt counts grouped by problem.

    Attempts deliberately come from raw submissions without a judgment join,
    matching problem-rating statistics: pending and unjudged submissions count.
    """
    statement = (
        select(
            _submissions.c.problem_id.label("problem_id"),
            func.count(func.distinct(_submissions.c.user_id)).label("attempted_users"),
        )
        .select_from(
            _submissions.join(
                arena_problems,
                arena_problems.c.id == _submissions.c.problem_id,
            )
        )
        .where(
            counts_toward_problem_rating(
                _submissions.c.user_id,
                arena_problems.c.owner_id,
            )
        )
    )
    if problem_ids is not None:
        statement = statement.where(_submissions.c.problem_id.in_(problem_ids))
    return statement.group_by(_submissions.c.problem_id).subquery()


def _solved_users_by_problem(problem_ids: set[str] | None) -> Subquery:
    """Return current participant solver counts grouped by problem."""
    statement = (
        select(
            arena_problem_solvers.c.problem_id.label("problem_id"),
            func.count().label("solved_users"),
        )
        .select_from(
            arena_problem_solvers.join(
                arena_problems,
                arena_problems.c.id == arena_problem_solvers.c.problem_id,
            )
        )
        .where(
            counts_toward_problem_rating(
                arena_problem_solvers.c.user_id,
                arena_problems.c.owner_id,
            )
        )
    )
    if problem_ids is not None:
        statement = statement.where(arena_problem_solvers.c.problem_id.in_(problem_ids))
    return statement.group_by(arena_problem_solvers.c.problem_id).subquery()


async def _qualifying_solves(
    session: AsyncSession,
    problem_ids: set[str] | None,
) -> list[tuple[str, str]]:
    """Return each qualifying ``(user_id, problem_id)`` in anchor order."""
    attempts = _attempted_users_by_problem(problem_ids)
    solves = _solved_users_by_problem(problem_ids)
    rows = (
        await session.execute(
            select(
                arena_problem_solvers.c.user_id,
                arena_problem_solvers.c.problem_id,
            )
            .select_from(
                arena_problem_solvers.join(
                    arena_problems,
                    arena_problems.c.id == arena_problem_solvers.c.problem_id,
                )
                .join(attempts, attempts.c.problem_id == arena_problem_solvers.c.problem_id)
                .join(solves, solves.c.problem_id == arena_problem_solvers.c.problem_id)
            )
            .where(
                counts_toward_problem_rating(
                    arena_problem_solvers.c.user_id,
                    arena_problems.c.owner_id,
                ),
                solves.c.solved_users * _ROCK_CRACKER_RATE_DENOMINATOR < attempts.c.attempted_users,
            )
            .order_by(
                arena_problem_solvers.c.solved_at,
                arena_problem_solvers.c.problem_id,
                arena_problem_solvers.c.user_id,
            )
        )
    ).all()
    return [(row.user_id, row.problem_id) for row in rows]


async def _current_holders(
    session: AsyncSession,
    qualifying_user_ids: set[str],
) -> dict[str, str | None]:
    """Return qualifying ROCK_CRACKER holders and their stored anchors."""
    if not qualifying_user_ids:
        return {}
    rows = (
        await session.execute(
            select(_user_badges.c.user_id, _user_badges.c.submission_id).where(
                _user_badges.c.badge == ArenaBadge.ROCK_CRACKER.value,
                _user_badges.c.user_id.in_(qualifying_user_ids),
            )
        )
    ).all()
    return {row.user_id: row.submission_id for row in rows}


async def reconcile_rock_cracker(
    session: AsyncSession,
    *,
    full_reconcile: bool,
    affected_problem_ids: set[str],
) -> tuple[int, int]:
    """Award current ROCK_CRACKER qualifiers and revoke on a full pass.

    A surviving holder keeps a non-NULL submission anchor even when another
    problem is now what keeps them eligible. The anchor records the solve that
    awarded the current badge row, not current evidence of qualification.
    """
    if not full_reconcile and not affected_problem_ids:
        return 0, 0

    problem_ids = None if full_reconcile else affected_problem_ids
    first_crack: dict[str, str] = {}
    for user_id, problem_id in await _qualifying_solves(session, problem_ids):
        first_crack.setdefault(user_id, problem_id)

    holders = await _current_holders(session, set(first_crack))
    pairs_needing_anchors = {
        (user_id, problem_id)
        for user_id, problem_id in first_crack.items()
        if user_id not in holders or holders[user_id] is None
    }
    anchors = await load_first_ac_submissions(session, pairs_needing_anchors)

    awarded = 0
    for user_id, problem_id in first_crack.items():
        if user_id in holders and holders[user_id] is not None:
            continue
        if await award_badge(
            session,
            user_id,
            ArenaBadge.ROCK_CRACKER,
            anchors.get((user_id, problem_id)),
        ):
            awarded += 1

    revoked = 0
    if full_reconcile:
        revoked = await revoke_badge_except(
            session,
            ArenaBadge.ROCK_CRACKER,
            set(first_crack),
        )
    return awarded, revoked
