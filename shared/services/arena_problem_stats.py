#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Rebuild precomputed Arena problem-statistics snapshots.

Aggregation rules:
  - Every metric excludes the problem owner's submissions and solves. Roles do
    not affect eligibility.
  - Verdict/language distributions use submissions with an active final verdict;
    time, memory, and wall-time histograms further restrict that set to AC.
  - Submission heatmaps use every submitted row, including pending and unjudged
    rows, bucketed by UTC calendar day.
  - Attempts count raw submissions through the originating first-AC submission:
    the solver's earliest currently-accepted submission, falling back to their
    earliest ever-accepted one when a rejudge revoked every AC. ``solved_at`` is
    deliberately not used for this -- it is frozen at the first solve while
    rejudges rewrite judgment completion times.
  - ``arena_problem_solvers`` is authoritative for the solver population; the
    first-AC submission behind each row is matched best-effort and only affects
    the attempt count.
  - First/last solver timestamps are first-AC judgment-completion times from
    ``arena_problem_solvers``.

Memory model:
  The rebuild walks the catalogue in batches of ``PROBLEM_STATS_BATCH_SIZE``
  problems and loads only that batch's submissions and solvers, so peak memory
  tracks the busiest batch rather than the deployment's entire submission
  history. The batch size is a module constant rather than a setting: the loop
  is a single-replica background worker with a coarse interval, and there is no
  operator-facing behaviour to tune.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import and_, case, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import languages as _languages
from shared.db_schema.arena import arena_problem_solvers as _solvers
from shared.db_schema.arena import arena_problem_statistics as _statistics
from shared.db_schema.arena import arena_problems as _problems
from shared.db_schema.arena import arena_submission_judgments as _judgments
from shared.db_schema.arena import arena_submissions as _submissions
from shared.db_schema.arena import arena_users as _users
from shared.enumerations import Verdict
from shared.services.arena_problem_stats_payload import (
    HISTOGRAM_BINS,
    ProblemSolverRow,
    ProblemSubmissionRow,
    build_problem_statistics_payload,
)
from shared.services.arena_query_helpers import active_arena_judgment_subquery, counts_toward_problem_rating

__all__ = ["HISTOGRAM_BINS", "PROBLEM_STATS_BATCH_SIZE", "compute_all_problem_statistics"]

_LOGGER = logging.getLogger(__name__)

PROBLEM_STATS_BATCH_SIZE = 100
"""Problems whose submissions and solvers are held in memory at once."""

_STREAM_PARTITION_SIZE = 5_000
"""Rows fetched per server-side cursor round trip while loading submissions."""


async def _load_submissions(session: AsyncSession, problem_ids: Sequence[str]) -> dict[str, list[ProblemSubmissionRow]]:
    """Load every non-owner submission of ``problem_ids`` with active-judgment metrics.

    Args:
        session: Active async database session.
        problem_ids: Problems whose submissions are wanted.

    Returns:
        dict: Problem id to its submission rows.
    """
    active_judgments = active_arena_judgment_subquery()
    statement = (
        select(
            _submissions.c.problem_id,
            _submissions.c.id,
            _submissions.c.user_id,
            _submissions.c.language_id,
            _submissions.c.created_at,
            _judgments.c.final_verdict,
            _judgments.c.max_wall_time_ms,
            _judgments.c.max_memory_kb,
        )
        .select_from(
            _submissions.join(_problems, _problems.c.id == _submissions.c.problem_id)
            .outerjoin(active_judgments, active_judgments.c.submission_id == _submissions.c.id)
            .outerjoin(
                _judgments,
                and_(
                    _judgments.c.submission_id == _submissions.c.id,
                    _judgments.c.created_at == active_judgments.c.max_created_at,
                ),
            )
        )
        .where(_submissions.c.problem_id.in_(problem_ids))
        .where(counts_toward_problem_rating(_submissions.c.user_id, _problems.c.owner_id))
    )

    by_problem: dict[str, list[ProblemSubmissionRow]] = defaultdict(list)
    # Submissions dominate this rebuild's memory, so they are read through a
    # server-side cursor in fixed partitions instead of one buffered ``.all()``.
    # This is the repository's only ``session.stream()`` user; the cursor must be
    # fully consumed before any other statement runs on this session.
    result = await session.stream(statement)
    async for rows in result.partitions(_STREAM_PARTITION_SIZE):
        for row in rows:
            by_problem[row.problem_id].append(
                ProblemSubmissionRow(
                    submission_id=row.id,
                    user_id=row.user_id,
                    language_id=row.language_id,
                    created_at=row.created_at,
                    verdict=row.final_verdict,
                    wall_time_ms=row.max_wall_time_ms,
                    memory_kb=row.max_memory_kb,
                )
            )
    return by_problem


async def _load_ac_submission_ids(session: AsyncSession, problem_ids: Sequence[str]) -> dict[tuple[str, str], str]:
    """Map each non-owner solver of ``problem_ids`` to the submission of their first AC.

    ``arena_problem_solvers`` does not store the submission id, so the link is
    derived, in two tiers, because a rejudge (changed limits or test cases) moves
    submissions across the AC boundary in both directions:

    1. the solver's earliest submission whose *current* judgment is AC. A
       submission that only became AC in a rejudge is accepted today, so it is
       that solver's first accepted submission today, and the rest of the
       snapshot reads current judgments the same way;
    2. failing that, their earliest submission that was *ever* AC. A solver row
       is never retracted, so a solver whose only AC was revoked by a rejudge
       still has a submission that earned the row, and attributing it beats
       dropping them out of the histogram.

    Correlating by ``finished_at == solved_at`` (the previous rule) is wrong in
    both directions once a problem is rejudged. ``solved_at`` is written only on
    the first solve and never revised, while a rejudge rewrites judgment
    completion times, so the two drift apart: on the live catalogue the equality
    matched 714 of 716 solver rows, and two of those 714 matched a later AC
    submission than the solver's own first, inflating its attempt count. A
    rejudge finishes its judgments in parallel, so "the AC judgment that
    completed first" is not "the earliest AC submission". The two tiers above
    match all 716.

    A solver the join still cannot match -- one with no AC judgment in any
    generation -- is kept by :func:`_load_solvers`.

    Args:
        session: Active async database session.
        problem_ids: Problems whose solvers are wanted.

    Returns:
        dict: ``(problem_id, user_id)`` to the matched AC submission id.
    """
    active_judgments = active_arena_judgment_subquery()
    # 0 for an AC that is the submission's current judgment, 1 for a superseded
    # one, so ascending order exhausts tier 1 before falling back to tier 2.
    tier = case((active_judgments.c.submission_id.is_(None), 1), else_=0)
    statement = (
        select(
            _solvers.c.problem_id,
            _solvers.c.user_id,
            _submissions.c.id.label("ac_submission_id"),
        )
        .select_from(
            _solvers.join(_problems, _problems.c.id == _solvers.c.problem_id)
            .join(
                _submissions,
                and_(
                    _submissions.c.problem_id == _solvers.c.problem_id,
                    _submissions.c.user_id == _solvers.c.user_id,
                ),
            )
            .join(
                _judgments,
                and_(
                    _judgments.c.submission_id == _submissions.c.id,
                    _judgments.c.final_verdict == Verdict.AC.value,
                ),
            )
            .outerjoin(
                active_judgments,
                and_(
                    active_judgments.c.submission_id == _judgments.c.submission_id,
                    active_judgments.c.max_created_at == _judgments.c.created_at,
                ),
            )
        )
        .where(_solvers.c.problem_id.in_(problem_ids))
        .where(counts_toward_problem_rating(_solvers.c.user_id, _problems.c.owner_id))
        .order_by(
            _solvers.c.problem_id,
            _solvers.c.user_id,
            tier,
            _submissions.c.created_at,
            _submissions.c.id,
        )
    )

    matched: dict[tuple[str, str], str] = {}
    for row in (await session.execute(statement)).all():
        matched.setdefault((row.problem_id, row.user_id), row.ac_submission_id)
    return matched


async def _load_solvers(
    session: AsyncSession, problem_ids: Sequence[str]
) -> tuple[dict[str, list[ProblemSolverRow]], int]:
    """Load every non-owner solver of ``problem_ids``, with its first-AC submission when known.

    ``arena_problem_solvers`` is authoritative for who solved a problem, so a
    solver whose originating submission cannot be identified is still returned
    (with ``ac_submission_id=None``) rather than dropped. Dropping it would make
    ``solver_count`` and the first/last milestones silently disagree with
    ``arena_problem_ratings.solved_users``.

    Args:
        session: Active async database session.
        problem_ids: Problems whose solvers are wanted.

    Returns:
        tuple: Problem id to its solver rows, and how many of those rows have no
        identifiable first-AC submission (the caller reports them once).
    """
    ac_submission_ids = await _load_ac_submission_ids(session, problem_ids)
    statement = (
        select(
            _solvers.c.problem_id,
            _solvers.c.user_id,
            _users.c.nome,
            _solvers.c.solved_at,
        )
        .select_from(
            _solvers.join(_problems, _problems.c.id == _solvers.c.problem_id).join(
                _users, _users.c.id == _solvers.c.user_id
            )
        )
        .where(_solvers.c.problem_id.in_(problem_ids))
        .where(counts_toward_problem_rating(_solvers.c.user_id, _problems.c.owner_id))
    )

    by_problem: dict[str, list[ProblemSolverRow]] = defaultdict(list)
    unmatched = 0
    for row in (await session.execute(statement)).all():
        ac_submission_id = ac_submission_ids.get((row.problem_id, row.user_id))
        if ac_submission_id is None:
            unmatched += 1
        by_problem[row.problem_id].append(
            ProblemSolverRow(
                user_id=row.user_id,
                name=row.nome,
                solved_at=row.solved_at,
                ac_submission_id=ac_submission_id,
            )
        )
    return by_problem, unmatched


async def compute_all_problem_statistics(session: AsyncSession) -> int:
    """Replace all per-problem statistics snapshots without committing.

    A snapshot is written for each problem with at least one non-owner
    submission, even when none of those submissions has a final verdict. The
    table is emptied once up front and refilled one problem batch at a time
    inside the caller's transaction, so readers never observe a partial rebuild.

    Args:
        session: Active async database session.

    Returns:
        int: Number of problem snapshots written.
    """
    time_limits = {
        row.id: row.time_limit_ms
        for row in (await session.execute(select(_problems.c.id, _problems.c.time_limit_ms))).all()
    }
    language_names = {
        row.id: row.name for row in (await session.execute(select(_languages.c.id, _languages.c.name))).all()
    }
    problem_ids = sorted(time_limits)

    await session.execute(delete(_statistics))
    written = 0
    unmatched_total = 0
    for start in range(0, len(problem_ids), PROBLEM_STATS_BATCH_SIZE):
        batch = problem_ids[start : start + PROBLEM_STATS_BATCH_SIZE]
        submissions_by_problem = await _load_submissions(session, batch)
        solvers_by_problem, unmatched = await _load_solvers(session, batch)
        unmatched_total += unmatched
        snapshots = [
            {
                "problem_id": problem_id,
                "data": build_problem_statistics_payload(
                    submissions,
                    solvers_by_problem.get(problem_id, []),
                    time_limits.get(problem_id, 0),
                    language_names,
                ),
            }
            for problem_id, submissions in submissions_by_problem.items()
        ]
        if snapshots:
            await session.execute(_statistics.insert(), snapshots)
            written += len(snapshots)

    if unmatched_total:
        _LOGGER.warning(
            "%d Arena solver row(s) have no matching first-AC submission; "
            "they still count as solvers but contribute no attempt count.",
            unmatched_total,
        )
    return written
