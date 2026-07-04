#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Catalogue-style Arena badge rules.

These rules award badges from per-problem and per-(user, problem) aggregate
facts such as language variety, first solves, and solve-rate difficulty.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena import arena_problem_ratings, arena_problem_solvers, arena_users
from shared.db_schema.arena import arena_submissions as _submissions
from shared.enumerations import ArenaBadge, ArenaRole
from shared.services.arena_badge_data import AcEvent, ac_join, award_badge
from shared.services.arena_query_helpers import active_arena_judgment_subquery

_LANGUAGE_THRESHOLDS: tuple[tuple[int, ArenaBadge], ...] = (
    (3, ArenaBadge.LANGUAGES_3),
    (5, ArenaBadge.LANGUAGES_5),
    (10, ArenaBadge.LANGUAGES_10),
)
_ROCK_CRACKER_SOLVE_RATE = 0.20


async def award_languages(session: AsyncSession, events: list[AcEvent]) -> int:
    """Award distinct-language badges for affected ``(user, problem)`` pairs."""
    pairs = {(e.user_id, e.problem_id) for e in events}
    if not pairs:
        return 0

    user_ids = {user_id for user_id, _ in pairs}
    problem_ids = {problem_id for _, problem_id in pairs}
    active = active_arena_judgment_subquery()
    rows = (
        await session.execute(
            select(
                _submissions.c.user_id,
                _submissions.c.problem_id,
                func.count(func.distinct(_submissions.c.language_id)).label("language_count"),
            )
            .select_from(ac_join(active))
            .where(_submissions.c.user_id.in_(user_ids), _submissions.c.problem_id.in_(problem_ids))
            .group_by(_submissions.c.user_id, _submissions.c.problem_id)
        )
    ).all()

    awarded = 0
    for row in rows:
        if (row.user_id, row.problem_id) not in pairs:
            continue
        for threshold, badge in _LANGUAGE_THRESHOLDS:
            if row.language_count >= threshold and await award_badge(session, row.user_id, badge):
                awarded += 1
    return awarded


async def award_first_solver(session: AsyncSession, events: list[AcEvent]) -> int:
    """Award FIRST_SOLVER to each affected problem's earliest ARENA_USER solver.

    ARENA_ADMIN and ARENA_JUDGE accounts are excluded: they can submit AC solutions
    (e.g. while authoring or testing a problem) without being eligible for the badge.
    """
    problem_ids = {e.problem_id for e in events}
    if not problem_ids:
        return 0

    rows = (
        await session.execute(
            select(
                arena_problem_solvers.c.problem_id,
                arena_problem_solvers.c.user_id,
                arena_problem_solvers.c.solved_at,
            )
            .select_from(arena_problem_solvers.join(arena_users, arena_users.c.id == arena_problem_solvers.c.user_id))
            .where(
                arena_problem_solvers.c.problem_id.in_(problem_ids),
                arena_users.c.role == ArenaRole.ARENA_USER,
            )
            .order_by(
                arena_problem_solvers.c.problem_id,
                arena_problem_solvers.c.solved_at,
                arena_problem_solvers.c.user_id,
            )
        )
    ).all()

    awarded = 0
    seen: set[str] = set()
    for row in rows:
        if row.problem_id in seen:
            continue
        seen.add(row.problem_id)
        if await award_badge(session, row.user_id, ArenaBadge.FIRST_SOLVER):
            awarded += 1
    return awarded


async def award_rock_cracker(session: AsyncSession, events: list[AcEvent]) -> int:
    """Award ROCK_CRACKER to solvers of low-solve-rate affected problems."""
    problem_ids = {e.problem_id for e in events}
    if not problem_ids:
        return 0

    hard_problem_ids = {
        row.problem_id
        for row in (
            await session.execute(
                select(
                    arena_problem_ratings.c.problem_id,
                    arena_problem_ratings.c.attempted_users,
                    arena_problem_ratings.c.solved_users,
                ).where(
                    arena_problem_ratings.c.problem_id.in_(problem_ids),
                    arena_problem_ratings.c.attempted_users > 0,
                    arena_problem_ratings.c.solved_users
                    < arena_problem_ratings.c.attempted_users * _ROCK_CRACKER_SOLVE_RATE,
                )
            )
        ).all()
    }
    if not hard_problem_ids:
        return 0

    awarded = 0
    rows = (
        await session.execute(
            select(arena_problem_solvers.c.user_id).where(arena_problem_solvers.c.problem_id.in_(hard_problem_ids))
        )
    ).all()
    for (user_id,) in rows:
        if await award_badge(session, user_id, ArenaBadge.ROCK_CRACKER):
            awarded += 1
    return awarded
