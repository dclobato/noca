#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Aggregate and dynamic badge rules: streaks, CLEAN_CODE, and FULL_CLEAR.

These rules need per-problem or per-user aggregates rather than a single
submission's history, so they live apart from the per-submission evaluator in
``arena_badges``. Data access helpers come from ``arena_badge_data``.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta

import pytz
from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena import arena_problem_set_problems, arena_problem_solvers
from shared.db_schema.arena import arena_submission_judgments as _judgments
from shared.db_schema.arena import arena_submissions as _submissions
from shared.db_schema.arena import arena_users as _users
from shared.enumerations import ArenaBadge
from shared.services.arena_badge_data import AcEvent, ac_join, as_utc, award_badge, timezone_name
from shared.services.arena_query_helpers import active_arena_judgment_subquery

_CLEAN_CODE_PERCENTILE = 0.05
_STRIKE_THRESHOLDS: tuple[tuple[int, ArenaBadge], ...] = (
    (3, ArenaBadge.STRIKE_3),
    (7, ArenaBadge.STRIKE_7),
    (30, ArenaBadge.STRIKE_30),
)
_PROBLEM_COUNT_THRESHOLDS: tuple[tuple[int, ArenaBadge], ...] = (
    (10, ArenaBadge.PROBLEMS_10),
    (25, ArenaBadge.PROBLEMS_25),
    (100, ArenaBadge.PROBLEMS_100),
    (500, ArenaBadge.PROBLEMS_500),
)


def consecutive_runs(days: list[date]) -> tuple[int, int]:
    """Return (run ending at the latest day, longest run anywhere) for sorted days.

    Args:
        days: Sorted, distinct solve-days.

    Returns:
        Tuple of the run length ending at the last day and the maximum run length.
    """
    if not days:
        return 0, 0
    longest = run = 1
    for prev, cur in zip(days, days[1:], strict=False):
        run = run + 1 if cur - prev == timedelta(days=1) else 1
        longest = max(longest, run)
    return run, longest


def percentile_threshold(values: list[int]) -> int | None:
    """Return the top-5% (lowest) threshold value, ties-inclusive, or None if empty.

    Args:
        values: Metric measurements (e.g. wall-time ms or memory kb).

    Returns:
        The value at the 5th-percentile rank (index ``ceil(0.05*n)-1``, min 0),
        or ``None`` when there are no measurements.
    """
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, math.ceil(_CLEAN_CODE_PERCENTILE * len(ordered)) - 1)
    return ordered[idx]


async def award_clean_code(session: AsyncSession, problem_ids: set[str]) -> int:
    """Award CLEAN_CODE to every user in the top 5% by time or memory per problem."""
    awarded = 0
    active = active_arena_judgment_subquery()
    for problem_id in problem_ids:
        rows = (
            await session.execute(
                select(_submissions.c.user_id, _judgments.c.max_wall_time_ms, _judgments.c.max_memory_kb)
                .select_from(ac_join(active))
                .where(_submissions.c.problem_id == problem_id)
            )
        ).all()
        if not rows:
            continue
        time_thr = percentile_threshold([r.max_wall_time_ms for r in rows if r.max_wall_time_ms is not None])
        mem_thr = percentile_threshold([r.max_memory_kb for r in rows if r.max_memory_kb is not None])
        best_wall: dict[str, int] = {}
        best_mem: dict[str, int] = {}
        for row in rows:
            if row.max_wall_time_ms is not None:
                best_wall[row.user_id] = min(best_wall.get(row.user_id, row.max_wall_time_ms), row.max_wall_time_ms)
            if row.max_memory_kb is not None:
                best_mem[row.user_id] = min(best_mem.get(row.user_id, row.max_memory_kb), row.max_memory_kb)
        for user_id in {r.user_id for r in rows}:
            qualifies = (time_thr is not None and best_wall.get(user_id, math.inf) <= time_thr) or (
                mem_thr is not None and best_mem.get(user_id, math.inf) <= mem_thr
            )
            if qualifies and await award_badge(session, user_id, ArenaBadge.CLEAN_CODE):
                awarded += 1
    return awarded


async def award_streaks(session: AsyncSession, events: list[AcEvent]) -> int:
    """Recompute per-user solve streaks and award STRIKE badges (historical max)."""
    awarded = 0
    by_user: dict[str, AcEvent] = {e.user_id: e for e in events}
    active = active_arena_judgment_subquery()
    for user_id, sample in by_user.items():
        tz = pytz.timezone(timezone_name(sample))
        created = (
            (
                await session.execute(
                    select(_submissions.c.created_at)
                    .select_from(ac_join(active))
                    .where(_submissions.c.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
        days = sorted({as_utc(c).astimezone(tz).date() for c in created})
        if not days:
            continue
        current, longest = consecutive_runs(days)
        await session.execute(
            update(_users)
            .where(_users.c.id == user_id)
            .values(
                current_streak=current,
                last_ac_date=days[-1],
                longest_streak=case((_users.c.longest_streak < longest, longest), else_=_users.c.longest_streak),
            )
        )
        for threshold, badge in _STRIKE_THRESHOLDS:
            if longest >= threshold and await award_badge(session, user_id, badge):
                awarded += 1
    return awarded


async def award_problem_counts(session: AsyncSession, events: list[AcEvent]) -> int:
    """Award N-distinct-problems badges from the user's solved-problem counts.

    Counts distinct solved problems per user from ``arena_problem_solvers`` (one row
    per ``(user_id, problem_id)``) and awards every crossed threshold. Only users in
    the current batch are counted: a user can cross a threshold only by a new AC, which
    produces an event; the periodic full reconcile re-evaluates all AC history.
    """
    user_ids = {e.user_id for e in events}
    if not user_ids:
        return 0
    counts: dict[str, int] = {
        row[0]: row[1]
        for row in (
            await session.execute(
                select(arena_problem_solvers.c.user_id, func.count())
                .where(arena_problem_solvers.c.user_id.in_(user_ids))
                .group_by(arena_problem_solvers.c.user_id)
            )
        ).all()
    }
    awarded = 0
    for user_id, count in counts.items():
        for threshold, badge in _PROBLEM_COUNT_THRESHOLDS:
            if count >= threshold and await award_badge(session, user_id, badge):
                awarded += 1
    return awarded


async def award_full_clear(session: AsyncSession, events: list[AcEvent], owned: dict[str, set[ArenaBadge]]) -> int:
    """Award FULL_CLEAR for fully-solved problem sets using precomputed membership.

    Three batch queries replace the per-submission lookups: problem→sets,
    set→problems, and the affected users' solved problems. Membership is then
    evaluated in memory for each (user, problem) pair in the batch.
    """
    problem_ids = {e.problem_id for e in events}
    user_ids = {e.user_id for e in events}
    problem_sets: dict[str, set[str]] = defaultdict(set)
    for set_id, problem_id in (
        await session.execute(
            select(arena_problem_set_problems.c.problem_set_id, arena_problem_set_problems.c.problem_id).where(
                arena_problem_set_problems.c.problem_id.in_(problem_ids)
            )
        )
    ).all():
        problem_sets[problem_id].add(set_id)

    set_ids = {sid for sids in problem_sets.values() for sid in sids}
    set_problems: dict[str, set[str]] = defaultdict(set)
    if set_ids:
        for set_id, problem_id in (
            await session.execute(
                select(arena_problem_set_problems.c.problem_set_id, arena_problem_set_problems.c.problem_id).where(
                    arena_problem_set_problems.c.problem_set_id.in_(set_ids)
                )
            )
        ).all():
            set_problems[set_id].add(problem_id)

    user_solved: dict[str, set[str]] = defaultdict(set)
    for user_id, problem_id in (
        await session.execute(
            select(arena_problem_solvers.c.user_id, arena_problem_solvers.c.problem_id).where(
                arena_problem_solvers.c.user_id.in_(user_ids)
            )
        )
    ).all():
        user_solved[user_id].add(problem_id)

    awarded = 0
    for user_id, problem_id in {(e.user_id, e.problem_id) for e in events}:
        if ArenaBadge.FULL_CLEAR in owned.get(user_id, set()):
            continue
        solved = user_solved.get(user_id, set())
        if any(set_problems[sid] and set_problems[sid] <= solved for sid in problem_sets.get(problem_id, set())):
            if await award_badge(session, user_id, ArenaBadge.FULL_CLEAR):
                awarded += 1
            owned.setdefault(user_id, set()).add(ArenaBadge.FULL_CLEAR)
    return awarded
