#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Aggregate badge rules: streaks, problem counts, and FULL_CLEAR.

These rules need per-problem or per-user aggregates rather than a single
submission's history, so they live apart from the per-submission evaluator in
``arena_badges``. Data access helpers come from ``arena_badge_data``. The
dynamic rules live in ``arena_badge_rules_cleancode`` and
``arena_badge_rules_rock_cracker``.

An aggregate badge is earned by a *set* of submissions crossing a threshold, so
"the submission that awarded it" is a convention rather than a fact. The
convention here is the submission that crossed the threshold -- the AC that
completed the streak, that reached the Nth distinct problem, that finished the
set -- and each rule below identifies it explicitly rather than picking any
member of the qualifying set.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

import pytz
from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena import arena_problem_set_problems, arena_problem_solvers
from shared.db_schema.arena import arena_submissions as _submissions
from shared.db_schema.arena import arena_users as _users
from shared.enumerations import ArenaBadge
from shared.services.arena_badge_data import (
    AcEvent,
    OwnedBadges,
    ac_join,
    as_utc,
    award_badge,
    is_anchored,
    load_first_ac_submissions,
    timezone_name,
)
from shared.services.arena_query_helpers import active_arena_judgment_subquery

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


def streak_crossing_days(days: list[date], thresholds: tuple[int, ...]) -> dict[int, date]:
    """Return the first day on which a run reaches each threshold, for sorted days.

    A STRIKE badge is earned by the longest run anywhere in the user's history,
    so its anchor is the day that run first reached the threshold -- not the
    user's latest solve, which may belong to an unrelated, shorter run.

    Args:
        days: Sorted, distinct solve-days.
        thresholds: Run lengths to locate.

    Returns:
        Threshold to the earliest day a run of that length ended on. A threshold
        no run ever reached is absent.
    """
    crossings: dict[int, date] = {}
    run = 0
    for index, day in enumerate(days):
        run = run + 1 if index and day - days[index - 1] == timedelta(days=1) else 1
        for threshold in thresholds:
            if run == threshold and threshold not in crossings:
                crossings[threshold] = day
    return crossings


async def award_streaks(session: AsyncSession, events: list[AcEvent]) -> int:
    """Recompute per-user solve streaks and award STRIKE badges (historical max).

    Each badge is anchored to the user's earliest AC on the day their longest run
    first reached that length: the submission that completed the streak.
    """
    awarded = 0
    by_user: dict[str, AcEvent] = {e.user_id: e for e in events}
    active = active_arena_judgment_subquery()
    for user_id, sample in by_user.items():
        tz = pytz.timezone(timezone_name(sample))
        rows = (
            await session.execute(
                select(_submissions.c.created_at, _submissions.c.id)
                .select_from(ac_join(active))
                .where(_submissions.c.user_id == user_id)
            )
        ).all()
        first_of_day: dict[date, tuple[datetime, str]] = {}
        for created_at, submission_id in rows:
            created = as_utc(created_at)
            day = created.astimezone(tz).date()
            if day not in first_of_day or (created, submission_id) < first_of_day[day]:
                first_of_day[day] = (created, submission_id)
        days = sorted(first_of_day)
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
        crossings = streak_crossing_days(days, tuple(threshold for threshold, _ in _STRIKE_THRESHOLDS))
        for threshold, badge in _STRIKE_THRESHOLDS:
            if longest < threshold:
                continue
            crossing_day = crossings.get(threshold)
            anchor = first_of_day[crossing_day][1] if crossing_day is not None else None
            if await award_badge(session, user_id, badge, anchor):
                awarded += 1
    return awarded


async def award_problem_counts(session: AsyncSession, events: list[AcEvent]) -> int:
    """Award N-distinct-problems badges from the user's solved-problem counts.

    Reads ``arena_problem_solvers`` (one row per ``(user_id, problem_id)``) in
    solve order and awards every crossed threshold. Only users in the current
    batch are counted: a user can cross a threshold only by a new AC, which
    produces an event; the periodic full reconcile re-evaluates all AC history.

    The rows are ordered rather than merely counted because each badge is
    anchored to the submission that crossed its threshold: the user's first AC on
    the Nth distinct problem they solved. ``arena_problem_solvers`` records only
    ``solved_at``, so the anchoring submissions are resolved in one batch query.
    """
    user_ids = {e.user_id for e in events}
    if not user_ids:
        return 0
    solved: dict[str, list[str]] = defaultdict(list)
    for user_id, problem_id in (
        await session.execute(
            select(arena_problem_solvers.c.user_id, arena_problem_solvers.c.problem_id)
            .where(arena_problem_solvers.c.user_id.in_(user_ids))
            .order_by(
                arena_problem_solvers.c.user_id,
                arena_problem_solvers.c.solved_at,
                arena_problem_solvers.c.problem_id,
            )
        )
    ).all():
        solved[user_id].append(problem_id)

    crossing_pairs = {
        (user_id, problems[threshold - 1])
        for user_id, problems in solved.items()
        for threshold, _ in _PROBLEM_COUNT_THRESHOLDS
        if len(problems) >= threshold
    }
    anchors = await load_first_ac_submissions(session, crossing_pairs)

    awarded = 0
    for user_id, problems in solved.items():
        for threshold, badge in _PROBLEM_COUNT_THRESHOLDS:
            if len(problems) < threshold:
                continue
            anchor = anchors.get((user_id, problems[threshold - 1]))
            if await award_badge(session, user_id, badge, anchor):
                awarded += 1
    return awarded


async def award_full_clear(session: AsyncSession, events: list[AcEvent], owned: OwnedBadges) -> int:
    """Award FULL_CLEAR for fully-solved problem sets using precomputed membership.

    Three batch queries replace the per-submission lookups: problem→sets,
    set→problems, and the affected users' solved problems. Membership is then
    evaluated in memory for each (user, problem) pair in the batch.

    The badge is anchored to the AC that finished the set. That is *not* the
    submission in the batch that triggered the evaluation: membership is judged
    against the user's solved set as it stands now, so the qualifying set is
    usually completed by some earlier problem. The anchor is therefore the user's
    first AC on whichever of the set's problems they solved last.

    When several sets qualify it is the one the user completed earliest, chosen
    across every set the batch puts in reach of them rather than per event. Two
    qualifying sets need share no problem, so an answer settled from one event's
    problem could not see the other set at all.

    A user who already holds the badge *with* an anchor is skipped; one holding
    it unanchored is still evaluated so the pass can fill it in.
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

    # Solve times, not just membership: the anchor is the last of a set's problems
    # the user solved, which is what completed the set.
    user_solved: dict[str, dict[str, datetime]] = defaultdict(dict)
    for user_id, problem_id, solved_at in (
        await session.execute(
            select(
                arena_problem_solvers.c.user_id,
                arena_problem_solvers.c.problem_id,
                arena_problem_solvers.c.solved_at,
            ).where(arena_problem_solvers.c.user_id.in_(user_ids))
        )
    ).all():
        user_solved[user_id][problem_id] = as_utc(solved_at)

    # Every set the batch puts in reach of a user, gathered before any is chosen.
    # Choosing per event would settle the answer from the first event's problem
    # alone and never see a set that shares no problem with it -- which is the set
    # the user may well have completed first.
    candidate_sets: dict[str, set[str]] = defaultdict(set)
    for event in events:
        if is_anchored(owned, event.user_id, ArenaBadge.FULL_CLEAR):
            continue
        candidate_sets[event.user_id] |= problem_sets.get(event.problem_id, set())

    completing: dict[str, str] = {}
    for user_id, set_ids_in_reach in candidate_sets.items():
        solved = user_solved.get(user_id, {})
        qualifying = [sid for sid in set_ids_in_reach if set_problems[sid] and set_problems[sid] <= solved.keys()]
        if not qualifying:
            continue
        # The set completed earliest, then the problem within it solved last.
        best_set = min(qualifying, key=lambda sid: (max(solved[p] for p in set_problems[sid]), sid))
        completing[user_id] = max(set_problems[best_set], key=lambda p: (solved[p], p))

    anchors = await load_first_ac_submissions(session, set(completing.items()))
    awarded = 0
    for user_id, problem_id in completing.items():
        anchor = anchors.get((user_id, problem_id))
        if await award_badge(session, user_id, ArenaBadge.FULL_CLEAR, anchor):
            awarded += 1
        owned.setdefault(user_id, {})[ArenaBadge.FULL_CLEAR] = anchor
    return awarded
