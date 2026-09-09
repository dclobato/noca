#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Catalogue-style Arena badge rules.

These rules award badges from per-problem and per-(user, problem) aggregate
facts such as language variety and first solves.

Each badge is anchored to the submission that earned it. FIRST_SOLVER reads
``arena_problem_solvers``, which stores only ``solved_at``, so it resolves the
anchor with :func:`load_first_ac_submissions`; the language badges identify the
AC that first reached each distinct-language threshold.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena import arena_problem_solvers, arena_problems
from shared.db_schema.arena import arena_submissions as _submissions
from shared.enumerations import ArenaBadge
from shared.services.arena_badge_data import AcEvent, ac_join, award_badge, load_first_ac_submissions
from shared.services.arena_query_helpers import active_arena_judgment_subquery

_LANGUAGE_THRESHOLDS: tuple[tuple[int, ArenaBadge], ...] = (
    (3, ArenaBadge.LANGUAGES_3),
    (5, ArenaBadge.LANGUAGES_5),
    (10, ArenaBadge.LANGUAGES_10),
)


async def award_languages(session: AsyncSession, events: list[AcEvent]) -> int:
    """Award distinct-language badges for affected ``(user, problem)`` pairs.

    The submissions are walked in ``(created_at, id)`` order rather than counted
    in SQL, so each badge can be anchored to the AC that first brought the pair's
    distinct-language count up to the threshold.
    """
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
                _submissions.c.language_id,
                _submissions.c.id,
            )
            .select_from(ac_join(active))
            .where(_submissions.c.user_id.in_(user_ids), _submissions.c.problem_id.in_(problem_ids))
            .order_by(_submissions.c.created_at, _submissions.c.id)
        )
    ).all()

    seen_languages: dict[tuple[str, str], set[str]] = defaultdict(set)
    # (user, badge) -> the submission that first reached the threshold.
    crossings: dict[tuple[str, ArenaBadge], str] = {}
    for row in rows:
        pair = (row.user_id, row.problem_id)
        if pair not in pairs:
            continue
        seen_languages[pair].add(row.language_id)
        for threshold, badge in _LANGUAGE_THRESHOLDS:
            if len(seen_languages[pair]) == threshold:
                crossings.setdefault((row.user_id, badge), row.id)

    awarded = 0
    for (user_id, badge), submission_id in crossings.items():
        if await award_badge(session, user_id, badge, submission_id):
            awarded += 1
    return awarded


async def award_first_solver(session: AsyncSession, events: list[AcEvent]) -> int:
    """Award FIRST_SOLVER to each affected problem's earliest non-owner solver.

    Eligibility is gated by problem ownership, not role: any user (regardless of
    role) who is the first to solve a problem they do not own earns the badge.
    The problem owner is excluded because their own AC solutions (e.g. while
    authoring or testing) are not eligible.
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
            .select_from(
                arena_problem_solvers.join(arena_problems, arena_problems.c.id == arena_problem_solvers.c.problem_id)
            )
            .where(
                arena_problem_solvers.c.problem_id.in_(problem_ids),
                arena_problem_solvers.c.user_id != arena_problems.c.owner_id,
            )
            .order_by(
                arena_problem_solvers.c.problem_id,
                arena_problem_solvers.c.solved_at,
                arena_problem_solvers.c.user_id,
            )
        )
    ).all()

    winners: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if row.problem_id in seen:
            continue
        seen.add(row.problem_id)
        winners.append((row.user_id, row.problem_id))

    anchors = await load_first_ac_submissions(session, set(winners))
    awarded = 0
    for user_id, problem_id in winners:
        if await award_badge(session, user_id, ArenaBadge.FIRST_SOLVER, anchors.get((user_id, problem_id))):
            awarded += 1
    return awarded
