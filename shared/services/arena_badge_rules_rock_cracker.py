#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The dynamic ROCK_CRACKER badge rule.

ROCK_CRACKER follows the current participant-only solve rate rather than the
batch-derived problem-rating cache. An incremental pass derives from the
complete current population of the affected problems and can only add holders; a
full pass derives the complete holder set, which is what lets the reconciliation
revoke and re-anchor.

The anchor is the user's first AC on the earliest-solved problem that qualifies
*now*. It therefore moves when the problem that used to keep a holder eligible
stops qualifying, which is the point: a surviving row must name work that still
earns the badge, not the solve that happened to award it once.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Subquery

from shared.db_schema.arena import arena_problem_solvers, arena_problems
from shared.db_schema.arena import arena_submissions as _submissions
from shared.enumerations import ArenaBadge
from shared.services.arena_badge_data import load_first_ac_submissions
from shared.services.arena_badge_writer import BadgeAwards
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


async def reconcile_rock_cracker(
    session: AsyncSession,
    *,
    full_reconcile: bool,
    affected_problem_ids: set[str],
) -> BadgeAwards:
    """Derive the ROCK_CRACKER holders, over the catalogue or the touched problems.

    Args:
        session: Active async session.
        full_reconcile: Whether to rank every problem rather than only the
            problems this cycle touched. Only a full pass produces the complete
            holder set the reconciliation may revoke against.
        affected_problem_ids: Problems named by this cycle's event batches.

    Returns:
        Each qualifying user mapped to their first AC on the earliest-solved
        problem that qualifies now. A user whose qualifying solve resolves to no
        live AC is absent.
    """
    if not full_reconcile and not affected_problem_ids:
        return {}

    problem_ids = None if full_reconcile else affected_problem_ids
    first_crack: dict[str, str] = {}
    for user_id, problem_id in await _qualifying_solves(session, problem_ids):
        first_crack.setdefault(user_id, problem_id)

    anchors = await load_first_ac_submissions(session, set(first_crack.items()))
    return {
        (user_id, ArenaBadge.ROCK_CRACKER): anchors[(user_id, problem_id)]
        for user_id, problem_id in first_crack.items()
        if (user_id, problem_id) in anchors
    }
