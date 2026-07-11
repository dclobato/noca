#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Precomputed Arena statistics.

The rating worker calls :func:`compute_all_problem_statistics` and
:func:`compute_all_user_statistics` periodically to aggregate submission data
into a single JSON snapshot per problem (``arena_problem_statistics``) and per
user (``arena_user_statistics``). The Arena statistics and public profile
pages read those snapshots directly so no heavy aggregate query runs during a
request.

Aggregation rules (confirmed product decisions):
  - Verdict and language distributions cover judged submissions that count toward
    a problem: the problem owner's own submissions are excluded, and user roles do
    not affect the counting rule. This matches the rating and public solver-count
    rules.
  - Per-language wall-time / peak-memory tables and the wall-time histogram
    cover **AC submissions only** (under the same exclusion).

Each submission contributes its most-recent non-``SUPERSEDED`` judgment (the
"active" judgment), mirroring the selection used by the Arena submission list.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any, cast

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import languages as _languages
from shared.db_schema.arena import arena_problem_statistics as _arena_problem_statistics
from shared.db_schema.arena import arena_problems as _arena_problems
from shared.db_schema.arena import arena_submission_judgments as _judgments
from shared.db_schema.arena import arena_submissions as _submissions
from shared.db_schema.arena import arena_user_statistics as _arena_user_statistics
from shared.enumerations import Verdict
from shared.services.arena_query_helpers import active_arena_judgment_subquery, counts_toward_problem_rating

HISTOGRAM_BINS = 20

# One judged submission: (language_id, final_verdict, wall_time_ms, memory_kb).
_SubmissionRow = tuple[str, str, int | None, int | None]


def _histogram_for(values: list[int], time_limit_ms: int) -> list[int]:
    """Bucket wall-time values into ``HISTOGRAM_BINS`` bins over ``[0, time_limit_ms]``.

    Args:
        values: Wall-time measurements in milliseconds.
        time_limit_ms: Upper bound of the histogram (problem time limit).

    Returns:
        list[int]: Per-bin counts, length ``HISTOGRAM_BINS``.
    """
    counts = [0] * HISTOGRAM_BINS
    if time_limit_ms <= 0:
        return counts
    for value in values:
        idx = int(value / time_limit_ms * HISTOGRAM_BINS)
        idx = max(0, min(HISTOGRAM_BINS - 1, idx))
        counts[idx] += 1
    return counts


def _build_payload(rows: list[_SubmissionRow], time_limit_ms: int, lang_names: dict[str, str]) -> dict[str, Any]:
    """Assemble the statistics payload for one problem from its judged rows.

    Args:
        rows: Rows of ``(language_id, final_verdict, wall_ms, memory_kb)`` for
            the problem's judged submissions.
        time_limit_ms: Problem time limit, used as the histogram upper bound.
        lang_names: Mapping of language id to display name.

    Returns:
        dict: JSON-serializable statistics payload.
    """
    verdict_counts: dict[str, int] = defaultdict(int)
    language_counts: dict[str, int] = defaultdict(int)
    ac_wall: dict[str, list[int]] = defaultdict(list)
    ac_memory: dict[str, list[int]] = defaultdict(list)

    for language_id, verdict, wall_ms, memory_kb in rows:
        verdict_counts[verdict] += 1
        language_counts[language_id] += 1
        if verdict == Verdict.AC.value:
            if wall_ms is not None:
                ac_wall[language_id].append(wall_ms)
            if memory_kb is not None:
                ac_memory[language_id].append(memory_kb)

    def _name(language_id: str) -> str:
        return lang_names.get(language_id, language_id)

    verdicts = [
        {"verdict": verdict, "count": count}
        for verdict, count in sorted(verdict_counts.items(), key=lambda kv: kv[1], reverse=True)
    ]
    languages = [
        {"language_id": lid, "name": _name(lid), "count": count}
        for lid, count in sorted(language_counts.items(), key=lambda kv: kv[1], reverse=True)
    ]

    time_stats = sorted(
        (
            {
                "language_id": lid,
                "name": _name(lid),
                "count": len(values),
                "avg_ms": round(statistics.fmean(values), 1),
                "stddev_ms": round(statistics.pstdev(values), 1) if len(values) > 1 else 0.0,
            }
            for lid, values in ac_wall.items()
        ),
        key=lambda row: cast(float, row["avg_ms"]),
    )
    memory_stats = sorted(
        (
            {
                "language_id": lid,
                "name": _name(lid),
                "count": len(values),
                "avg_kb": round(statistics.fmean(values), 1),
                "stddev_kb": round(statistics.pstdev(values), 1) if len(values) > 1 else 0.0,
            }
            for lid, values in ac_memory.items()
        ),
        key=lambda row: cast(float, row["avg_kb"]),
    )
    histogram = [
        {"language_id": lid, "name": _name(lid), "counts": _histogram_for(values, time_limit_ms)}
        for lid, values in sorted(ac_wall.items(), key=lambda kv: _name(kv[0]).lower())
    ]

    return {
        "total_submissions": len(rows),
        "verdicts": verdicts,
        "languages": languages,
        "time_stats": time_stats,
        "memory_stats": memory_stats,
        "time_limit_ms": time_limit_ms,
        "histogram_bins": HISTOGRAM_BINS,
        "wall_time_histogram": histogram,
    }


async def compute_all_problem_statistics(session: AsyncSession) -> int:
    """Recompute and persist per-problem statistics snapshots.

    Replaces every row in ``arena_problem_statistics`` with a freshly computed
    snapshot for each problem that has at least one judged submission. Does
    **not** commit; the caller owns the transaction.

    Args:
        session: Active async database session.

    Returns:
        int: Number of problems with a statistics snapshot written.
    """
    time_limits = {
        row.id: row.time_limit_ms
        for row in (await session.execute(select(_arena_problems.c.id, _arena_problems.c.time_limit_ms))).all()
    }
    lang_names = {row.id: row.name for row in (await session.execute(select(_languages.c.id, _languages.c.name))).all()}

    active = active_arena_judgment_subquery()
    stmt = (
        select(
            _submissions.c.problem_id,
            _submissions.c.language_id,
            _judgments.c.final_verdict,
            _judgments.c.max_wall_time_ms,
            _judgments.c.max_memory_kb,
        )
        .select_from(
            _submissions.join(active, active.c.submission_id == _submissions.c.id)
            .join(
                _judgments,
                and_(
                    _judgments.c.submission_id == _submissions.c.id,
                    _judgments.c.created_at == active.c.max_created_at,
                ),
            )
            .join(_arena_problems, _arena_problems.c.id == _submissions.c.problem_id)
        )
        .where(
            _judgments.c.final_verdict.isnot(None),
            counts_toward_problem_rating(_submissions.c.user_id, _arena_problems.c.owner_id),
        )
    )

    by_problem: dict[str, list[_SubmissionRow]] = defaultdict(list)
    for row in (await session.execute(stmt)).all():
        by_problem[row.problem_id].append((row.language_id, row.final_verdict, row.max_wall_time_ms, row.max_memory_kb))

    await session.execute(delete(_arena_problem_statistics))
    for problem_id, rows in by_problem.items():
        payload = _build_payload(rows, time_limits.get(problem_id, 0), lang_names)
        await session.execute(_arena_problem_statistics.insert().values(problem_id=problem_id, data=payload))

    return len(by_problem)


def _build_user_payload(rows: list[tuple[str, str]], lang_names: dict[str, str]) -> dict[str, Any]:
    """Assemble the per-user statistics payload from judged submission rows.

    Args:
        rows: Rows of ``(language_id, final_verdict)`` for the user's judged
            submissions.
        lang_names: Mapping of language id to display name.

    Returns:
        dict: JSON-serializable statistics payload.
    """
    verdict_counts: dict[str, int] = defaultdict(int)
    language_counts: dict[str, int] = defaultdict(int)

    for language_id, verdict in rows:
        verdict_counts[verdict] += 1
        language_counts[language_id] += 1

    def _name(language_id: str) -> str:
        return lang_names.get(language_id, language_id)

    verdicts = [
        {"verdict": verdict, "count": count}
        for verdict, count in sorted(verdict_counts.items(), key=lambda kv: kv[1], reverse=True)
    ]
    languages = [
        {"language_id": lid, "name": _name(lid), "count": count}
        for lid, count in sorted(language_counts.items(), key=lambda kv: kv[1], reverse=True)
    ]

    return {
        "total_submissions": len(rows),
        "verdicts": verdicts,
        "languages": languages,
    }


async def compute_all_user_statistics(session: AsyncSession) -> int:
    """Recompute and persist per-user statistics snapshots.

    Replaces every row in ``arena_user_statistics`` with a freshly computed
    snapshot for each user that has at least one judged submission. All
    submissions are counted (no rating exclusion): these are the user's own
    submissions. Does **not** commit; the caller owns the transaction.

    Args:
        session: Active async database session.

    Returns:
        int: Number of users with a statistics snapshot written.
    """
    lang_names = {row.id: row.name for row in (await session.execute(select(_languages.c.id, _languages.c.name))).all()}

    active = active_arena_judgment_subquery()
    stmt = (
        select(
            _submissions.c.user_id,
            _submissions.c.language_id,
            _judgments.c.final_verdict,
        )
        .select_from(
            _submissions.join(active, active.c.submission_id == _submissions.c.id).join(
                _judgments,
                and_(
                    _judgments.c.submission_id == _submissions.c.id,
                    _judgments.c.created_at == active.c.max_created_at,
                ),
            )
        )
        .where(_judgments.c.final_verdict.isnot(None))
    )

    by_user: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in (await session.execute(stmt)).all():
        by_user[row.user_id].append((row.language_id, row.final_verdict))

    # Full rebuild: delete all rows then re-insert. Between the delete and the
    # caller's commit, concurrent reads return None (empty payload). This is the
    # same intentional trade-off as compute_all_problem_statistics; the read side
    # renders an "empty" state gracefully. Switch to upsert if the gap becomes a
    # concern at higher traffic.
    await session.execute(delete(_arena_user_statistics))
    for user_id, rows in by_user.items():
        payload = _build_user_payload(rows, lang_names)
        await session.execute(_arena_user_statistics.insert().values(user_id=user_id, data=payload))

    return len(by_user)
