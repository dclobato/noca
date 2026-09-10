#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Problem-set scoped Arena badge rules.

Both rules rank concrete submissions, so each badge names the very submission
that won its ``(problem_set, problem)`` pair -- no convention is involved. Both
return the badges they derive rather than writing them, and both derive from the
*live* Accepted set, so a rejudge that moves the winning submission off Accepted
moves the badge to the next-ranked solver on the following full pass rather than
leaving a pair with two holders.

A user can win several pairs and a badge row can name only one submission. The
canonical anchor is the earliest winning submission by ``(created_at, id)``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena import arena_problem_set_problems, arena_problem_sets
from shared.db_schema.arena import arena_submissions as _submissions
from shared.enumerations import ArenaBadge
from shared.services.arena_badge_data import AcEvent, ac_join, as_utc
from shared.services.arena_badge_writer import BadgeAwards
from shared.services.arena_query_helpers import active_arena_judgment_subquery


async def award_first_to_hand_in(session: AsyncSession, events: list[AcEvent]) -> BadgeAwards:
    """Derive FIRST_TO_HAND_IN for affected opted-in problem-set submissions.

    The winner is the earliest Accepted submission for each
    ``(problem_set, problem)`` pair, taken from the live AC set: ``ac_join``
    reads each submission's latest judgment, so a rejudged winner drops out of
    the ranking and the next-earliest solver takes the pair.
    """
    pairs = _event_set_pairs(events)
    if not pairs:
        return {}

    pairs = await _mapped_pairs(session, pairs)
    if not pairs:
        return {}

    rows = await _load_set_ac_rows(session, {pair[0] for pair in pairs}, {pair[1] for pair in pairs})
    winners: list[tuple[str, datetime, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        pair = (row.problem_set_id, row.problem_id)
        if pair not in pairs or pair in seen:
            continue
        seen.add(pair)
        winners.append((row.user_id, as_utc(row.created_at), row.submission_id))
    return _earliest_per_user(winners, ArenaBadge.FIRST_TO_HAND_IN)


async def award_almost_late(
    session: AsyncSession,
    events: list[AcEvent],
    *,
    now: datetime,
    full_reconcile: bool,
) -> BadgeAwards:
    """Derive ALMOST_LATE for the latest on-time solver after set deadlines pass.

    Mirrors :func:`award_first_to_hand_in`, including its live-AC ranking: the
    winner is whoever is last on time among the submissions that are Accepted
    now, so a rejudge moves the badge rather than duplicating it.
    """
    pairs = await _deadline_pairs(session, events, now=now, full_reconcile=full_reconcile)
    if not pairs:
        return {}

    rows = await _load_set_ac_rows(session, {pair[0] for pair in pairs}, {pair[1] for pair in pairs})
    deadlines = dict(pairs)
    latest: dict[tuple[str, str], tuple[datetime, str, str]] = {}
    for row in rows:
        pair = (row.problem_set_id, row.problem_id)
        deadline = deadlines.get(pair)
        if deadline is None or as_utc(row.created_at) > deadline:
            continue
        key = (as_utc(row.created_at), row.submission_id)
        if pair not in latest or key > (latest[pair][0], latest[pair][1]):
            latest[pair] = (key[0], key[1], row.user_id)

    return _earliest_per_user(
        [(user_id, created_at, submission_id) for created_at, submission_id, user_id in latest.values()],
        ArenaBadge.ALMOST_LATE,
    )


def _earliest_per_user(winners: list[tuple[str, datetime, str]], badge: ArenaBadge) -> BadgeAwards:
    """Keep each user's earliest winning submission, by ``(created_at, id)``."""
    best: dict[str, tuple[datetime, str]] = {}
    for user_id, created_at, submission_id in winners:
        key = (created_at, submission_id)
        if user_id not in best or key < best[user_id]:
            best[user_id] = key
    return {(user_id, badge): submission_id for user_id, (_, submission_id) in best.items()}


async def _deadline_pairs(
    session: AsyncSession,
    events: list[AcEvent],
    *,
    now: datetime,
    full_reconcile: bool,
) -> dict[tuple[str, str], datetime]:
    """Return eligible ``(problem_set, problem)`` pairs and their deadlines."""
    if full_reconcile:
        rows = (
            await session.execute(
                select(
                    arena_problem_set_problems.c.problem_set_id,
                    arena_problem_set_problems.c.problem_id,
                    arena_problem_sets.c.deadline,
                )
                .select_from(
                    arena_problem_set_problems.join(
                        arena_problem_sets,
                        arena_problem_sets.c.id == arena_problem_set_problems.c.problem_set_id,
                    )
                )
                .where(arena_problem_sets.c.deadline.isnot(None), arena_problem_sets.c.deadline <= now)
            )
        ).all()
    else:
        event_pairs = _event_set_pairs(events)
        if not event_pairs:
            return {}
        rows = (
            await session.execute(
                select(
                    arena_problem_set_problems.c.problem_set_id,
                    arena_problem_set_problems.c.problem_id,
                    arena_problem_sets.c.deadline,
                )
                .select_from(
                    arena_problem_set_problems.join(
                        arena_problem_sets,
                        arena_problem_sets.c.id == arena_problem_set_problems.c.problem_set_id,
                    )
                )
                .where(
                    arena_problem_sets.c.deadline.isnot(None),
                    arena_problem_sets.c.deadline <= now,
                    arena_problem_set_problems.c.problem_set_id.in_({pair[0] for pair in event_pairs}),
                    arena_problem_set_problems.c.problem_id.in_({pair[1] for pair in event_pairs}),
                )
            )
        ).all()
        rows = [row for row in rows if (row.problem_set_id, row.problem_id) in event_pairs]
    return {(row.problem_set_id, row.problem_id): as_utc(row.deadline) for row in rows}


def _event_set_pairs(events: list[AcEvent]) -> set[tuple[str, str]]:
    """Return concrete ``(problem_set_id, problem_id)`` pairs from events."""
    pairs: set[tuple[str, str]] = set()
    for event in events:
        if event.problem_set_id is not None:
            pairs.add((event.problem_set_id, event.problem_id))
    return pairs


async def _mapped_pairs(session: AsyncSession, pairs: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """Filter pairs to those declared in ``arena_problem_set_problems``."""
    rows = (
        await session.execute(
            select(arena_problem_set_problems.c.problem_set_id, arena_problem_set_problems.c.problem_id).where(
                arena_problem_set_problems.c.problem_set_id.in_({pair[0] for pair in pairs}),
                arena_problem_set_problems.c.problem_id.in_({pair[1] for pair in pairs}),
            )
        )
    ).all()
    return {(row.problem_set_id, row.problem_id) for row in rows if (row.problem_set_id, row.problem_id) in pairs}


async def _load_set_ac_rows(session: AsyncSession, set_ids: set[str], problem_ids: set[str]) -> Sequence[Any]:
    """Load Accepted submissions tied to the affected problem-set/problem pairs."""
    active = active_arena_judgment_subquery()
    return (
        await session.execute(
            select(
                _submissions.c.problem_set_id,
                _submissions.c.problem_id,
                _submissions.c.user_id,
                _submissions.c.id.label("submission_id"),
                _submissions.c.created_at,
            )
            .select_from(ac_join(active))
            .where(
                _submissions.c.problem_set_id.in_(set_ids),
                _submissions.c.problem_id.in_(problem_ids),
                _submissions.c.problem_set_id.isnot(None),
            )
            .order_by(
                _submissions.c.problem_set_id,
                _submissions.c.problem_id,
                _submissions.c.created_at,
                _submissions.c.id,
            )
        )
    ).all()
