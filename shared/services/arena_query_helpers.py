#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reusable SQLAlchemy Core query fragments for Arena submission judgments.

Centralizes query idioms that several Arena consumers would otherwise open-code,
keeping the "active judgment" definition in one place so callers cannot drift.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import ColumnElement, Select, and_, func, select
from sqlalchemy.sql.selectable import Join, Subquery

from shared.db_schema.arena import arena_submission_judgments, arena_submissions
from shared.enumerations import JudgmentStatus, Verdict

_SUPERSEDED = JudgmentStatus.SUPERSEDED.value


def counts_toward_problem_rating(
    user_id_col: ColumnElement[Any],
    owner_id: Any,
) -> ColumnElement[bool]:
    """SQL predicate selecting submissions/solves that count toward a problem.

    A submitter contributes to a problem's rating inputs, public solver count,
    and statistics snapshot unless they own the problem. User roles do not
    change this rule. This is the single source of truth for that policy; use it
    in ``WHERE`` clauses joined against ``arena_problems`` for the owner.

    Args:
        user_id_col: Column holding the submitter's user id (e.g. a submission or
            solver ``user_id``).
        owner_id: The problem's ``owner_id`` (a literal value or column).

    Returns:
        ColumnElement[bool]: True for rows that should be counted.
    """
    return cast(ColumnElement[bool], user_id_col != owner_id)


def is_excluded_from_problem_rating(user_id: str, owner_id: str | None) -> bool:
    """Return whether a user must NOT count toward a problem's rating stats.

    Scalar counterpart of :func:`counts_toward_problem_rating` for Python call
    sites that already hold the owner value. A user is excluded only when they
    own the problem.

    Args:
        user_id: The submitting user's id.
        owner_id: The problem owner's id.

    Returns:
        bool: True when the user must be excluded from rating counters.
    """
    return user_id == owner_id


def active_arena_judgment_subquery() -> Subquery:
    """Return the most-recent non-superseded judgment timestamp per submission.

    The result is a subquery selecting ``submission_id`` and ``max_created_at``,
    intended to be joined back against ``arena_submission_judgments`` on
    ``(submission_id, created_at)`` to pick the active judgment row.

    Returns:
        Subquery: Subquery with ``submission_id`` and ``max_created_at`` columns.
    """
    return (
        select(
            arena_submission_judgments.c.submission_id,
            func.max(arena_submission_judgments.c.created_at).label("max_created_at"),
        )
        .where(arena_submission_judgments.c.status != _SUPERSEDED)
        .group_by(arena_submission_judgments.c.submission_id)
        .subquery()
    )


def _live_ac_join(user_id: str | None, problem_id: str | None) -> Join:
    """Join submissions to the active judgment, kept only when it is Accepted.

    The active judgment is each submission's most recent non-superseded one --
    the same view the badge rules use, so "still Accepted" means one thing
    across the judge, the rating worker and the badge rules.

    The grouped subquery is scoped to the pair whenever the caller names one.
    The unscoped aggregate is right for a whole-corpus pass and wrong on the
    judge's per-judgment path, where it would run a full-table aggregate for
    every settled submission.

    Args:
        user_id: Restrict to one solver, or None for all.
        problem_id: Restrict to one problem, or None for all.

    Returns:
        Join: submissions joined to their active, Accepted, DONE judgment.
    """
    active = select(
        arena_submission_judgments.c.submission_id,
        func.max(arena_submission_judgments.c.created_at).label("max_created_at"),
    ).where(arena_submission_judgments.c.status != _SUPERSEDED)
    if user_id is not None or problem_id is not None:
        scope = select(arena_submissions.c.id)
        if user_id is not None:
            scope = scope.where(arena_submissions.c.user_id == user_id)
        if problem_id is not None:
            scope = scope.where(arena_submissions.c.problem_id == problem_id)
        active = active.where(arena_submission_judgments.c.submission_id.in_(scope))
    grouped = active.group_by(arena_submission_judgments.c.submission_id).subquery()
    return arena_submissions.join(grouped, grouped.c.submission_id == arena_submissions.c.id).join(
        arena_submission_judgments,
        and_(
            arena_submission_judgments.c.submission_id == arena_submissions.c.id,
            arena_submission_judgments.c.created_at == grouped.c.max_created_at,
            arena_submission_judgments.c.final_verdict == Verdict.AC.value,
            arena_submission_judgments.c.status == JudgmentStatus.DONE.value,
        ),
    )


def first_live_ac_per_pair_select(
    *,
    user_id: str | None = None,
    problem_id: str | None = None,
) -> Select[tuple[str, str, datetime]]:
    """Return each ``(user, problem)`` pair's first still-Accepted judgment time.

    The *first* AC is the earliest submission that is still Accepted, and the
    value reported is when its judgment completed -- the documented meaning of
    ``arena_problem_solvers.solved_at``. Ranking by submission rather than by
    completion matters after a rejudge, where an earlier submission's
    replacement judgment can finish after a later submission's.

    This is the single definition of a solver row's correct content. The judge
    applies it to one pair on every finishing judgment and the one-off
    reconciliation script applies it to the whole corpus; they must not drift.

    Args:
        user_id: Restrict to one solver, or None for all.
        problem_id: Restrict to one problem, or None for all.

    Returns:
        Select: Yields ``(user_id, problem_id, solved_at)``, one row per pair
        that still holds an Accepted submission.
    """
    ranked = (
        select(
            arena_submissions.c.user_id,
            arena_submissions.c.problem_id,
            arena_submission_judgments.c.finished_at.label("solved_at"),
            func.row_number()
            .over(
                partition_by=(arena_submissions.c.user_id, arena_submissions.c.problem_id),
                order_by=(arena_submissions.c.created_at, arena_submissions.c.id),
            )
            .label("ac_rank"),
        )
        .select_from(_live_ac_join(user_id, problem_id))
        .where(arena_submission_judgments.c.finished_at.isnot(None))
        .subquery()
    )
    statement = select(ranked.c.user_id, ranked.c.problem_id, ranked.c.solved_at).where(ranked.c.ac_rank == 1)
    if user_id is not None:
        statement = statement.where(ranked.c.user_id == user_id)
    if problem_id is not None:
        statement = statement.where(ranked.c.problem_id == problem_id)
    return statement


def first_live_ac_solved_at_select(user_id: str, problem_id: str) -> Select[tuple[datetime]]:
    """Return the ``solved_at`` one pair should carry, scalar-shaped for the judge.

    Scoped single-pair form of :func:`first_live_ac_per_pair_select`, so the
    judge can ``scalar()`` it directly on the pair it has in hand.

    Args:
        user_id: The solver's Arena user id.
        problem_id: UUID of the Arena problem.

    Returns:
        Select: Yields the ``finished_at`` of the pair's first live AC, or no
        row at all when the pair holds no Accepted submission any more.
    """
    pair = first_live_ac_per_pair_select(user_id=user_id, problem_id=problem_id).subquery()
    return select(pair.c.solved_at)
