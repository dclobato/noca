#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Service for freezing and reading Arena problem-set rating snapshots.

After a problem set's deadline passes, :func:`snapshot_problem_set` freezes, per
user who submitted anything to the set, the sum of the *current* ratings of the
problems they got AC on. Two snapshot tables capture this (per-user totals and
per (user, problem) AC ratings). The function is idempotent: re-running it
replaces the set's snapshot rows.

All functions take an explicit ``AsyncSession`` and never commit; the caller owns
the transaction boundary (``session.flush()`` is used internally).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.services.arena_problem_set_service import (
    ArenaProblemSetValidationError,
    _assert_teacher,
    _load_set_and_class,
    _set_problem_ids,
    _set_tied_verdicts,
)
from shared.db_schema.arena import (
    arena_problem_ratings,
    arena_problem_set_problem_snapshots,
    arena_problem_set_user_snapshots,
    arena_users,
)
from shared.enumerations import ArenaRole, Verdict


@dataclass(frozen=True)
class SnapshotUserTotal:
    """A user's frozen total rating for a problem set."""

    user_id: str
    user_name: str
    total_rating: int


@dataclass(frozen=True)
class SnapshotProblemRating:
    """A user's frozen rating for one AC'd problem in a problem set."""

    user_id: str
    user_name: str
    problem_id: str
    rating: int


async def _problem_ratings(session: AsyncSession, problem_ids: set[str]) -> dict[str, int]:
    """Return current rating per problem id (0 when no rating row exists)."""
    if not problem_ids:
        return {}
    rows = await session.execute(
        select(arena_problem_ratings.c.problem_id, arena_problem_ratings.c.rating).where(
            arena_problem_ratings.c.problem_id.in_(problem_ids)
        )
    )
    return {problem_id: rating for problem_id, rating in rows.all()}


async def snapshot_problem_set(session: AsyncSession, *, set_id: str, now: datetime) -> int:
    """Freeze the rating snapshot for a problem set; idempotent per set.

    For every user with a set-tied submission, inserts a per-user total (sum of
    current ratings of the problems they got AC on, 0 if none) and one per-problem
    row for each such AC'd problem.

    Args:
        session: Active async database session (caller commits).
        set_id: Problem set UUID.
        now: Snapshot timestamp.

    Returns:
        The number of users captured in the snapshot.

    Raises:
        ArenaProblemSetValidationError: When the set has no deadline.
    """
    problem_set, _arena_class = await _load_set_and_class(session, set_id)
    if problem_set.deadline is None:
        raise ArenaProblemSetValidationError("Cannot snapshot a problem set without a deadline.")

    await session.execute(
        delete(arena_problem_set_user_snapshots).where(arena_problem_set_user_snapshots.c.problem_set_id == set_id)
    )
    await session.execute(
        delete(arena_problem_set_problem_snapshots).where(
            arena_problem_set_problem_snapshots.c.problem_set_id == set_id
        )
    )

    set_problems = await _set_problem_ids(session, set_id)
    ratings = await _problem_ratings(session, set_problems)
    grouped = await _set_tied_verdicts(session, set_id)

    users: dict[str, list[str]] = {}
    for (user_id, problem_id), verdicts in grouped.items():
        users.setdefault(user_id, [])
        if problem_id in set_problems and Verdict.AC.value in verdicts:
            users[user_id].append(problem_id)

    for user_id, ac_problems in users.items():
        total = sum(ratings.get(problem_id, 0) for problem_id in ac_problems)
        await session.execute(
            arena_problem_set_user_snapshots.insert().values(
                problem_set_id=set_id,
                user_id=user_id,
                total_rating=total,
                snapshot_at=now,
            )
        )
        for problem_id in ac_problems:
            await session.execute(
                arena_problem_set_problem_snapshots.insert().values(
                    problem_set_id=set_id,
                    user_id=user_id,
                    problem_id=problem_id,
                    rating=ratings.get(problem_id, 0),
                )
            )
    await session.flush()
    return len(users)


async def list_snapshot_user_totals(
    session: AsyncSession, *, actor_id: str, actor_role: ArenaRole, set_id: str
) -> list[SnapshotUserTotal]:
    """Read the per-user frozen totals for a set (assigned teacher/admin)."""
    _problem_set, arena_class = await _load_set_and_class(session, set_id)
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)
    rows = await session.execute(
        select(
            arena_problem_set_user_snapshots.c.user_id,
            arena_users.c.nome.label("user_name"),
            arena_problem_set_user_snapshots.c.total_rating,
        )
        .select_from(
            arena_problem_set_user_snapshots.join(
                arena_users, arena_problem_set_user_snapshots.c.user_id == arena_users.c.id
            )
        )
        .where(arena_problem_set_user_snapshots.c.problem_set_id == set_id)
        .order_by(arena_problem_set_user_snapshots.c.total_rating.desc())
    )
    return [SnapshotUserTotal(user_id=r.user_id, user_name=r.user_name, total_rating=r.total_rating) for r in rows]


async def list_snapshot_ratings(
    session: AsyncSession, *, actor_id: str, actor_role: ArenaRole, set_id: str
) -> list[SnapshotProblemRating]:
    """Read the per (user, problem) frozen AC ratings for a set (assigned teacher/admin)."""
    _problem_set, arena_class = await _load_set_and_class(session, set_id)
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)
    rows = await session.execute(
        select(
            arena_problem_set_problem_snapshots.c.user_id,
            arena_users.c.nome.label("user_name"),
            arena_problem_set_problem_snapshots.c.problem_id,
            arena_problem_set_problem_snapshots.c.rating,
        )
        .select_from(
            arena_problem_set_problem_snapshots.join(
                arena_users, arena_problem_set_problem_snapshots.c.user_id == arena_users.c.id
            )
        )
        .where(arena_problem_set_problem_snapshots.c.problem_set_id == set_id)
        .order_by(arena_users.c.nome, arena_problem_set_problem_snapshots.c.problem_id)
    )
    return [
        SnapshotProblemRating(
            user_id=r.user_id,
            user_name=r.user_name,
            problem_id=r.problem_id,
            rating=r.rating,
        )
        for r in rows
    ]
