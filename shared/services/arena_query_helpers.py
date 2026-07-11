#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reusable SQLAlchemy Core query fragments for Arena submission judgments.

Centralizes query idioms that several Arena consumers would otherwise open-code,
keeping the "active judgment" definition in one place so callers cannot drift.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.sql.selectable import Subquery

from shared.db_schema.arena import arena_submission_judgments
from shared.enumerations import JudgmentStatus

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
