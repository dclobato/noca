#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Pure payload builders for precomputed Arena problem statistics."""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from shared.db_datetime import as_utc, utc_day
from shared.enumerations import Verdict

HISTOGRAM_BINS = 20
ATTEMPT_BINS: tuple[tuple[str, int, int | None], ...] = (
    ("1", 1, 1),
    ("2", 2, 2),
    ("3", 3, 3),
    ("4", 4, 4),
    ("5", 5, 5),
    ("6-10", 6, 10),
    ("11-15", 11, 15),
    ("16+", 16, None),
)


@dataclass(frozen=True, slots=True)
class ProblemSubmissionRow:
    """One non-owner submission and its optional active judgment metrics."""

    submission_id: str
    user_id: str
    language_id: str
    created_at: datetime
    verdict: str | None
    wall_time_ms: int | None
    memory_kb: int | None


@dataclass(frozen=True, slots=True)
class ProblemSolverRow:
    """One solver and, when identifiable, the submission that produced their first AC.

    ``ac_submission_id`` is ``None`` for a solver whose originating AC judgment
    could not be matched. Such a solver still counts as a solver; it simply
    contributes no attempt count.
    """

    user_id: str
    name: str
    solved_at: datetime
    ac_submission_id: str | None


def _histogram_for(values: list[int], time_limit_ms: int) -> list[int]:
    """Bucket wall-time values into fixed bins over ``[0, time_limit_ms]``."""
    counts = [0] * HISTOGRAM_BINS
    if time_limit_ms <= 0:
        return counts
    for value in values:
        index = int(value / time_limit_ms * HISTOGRAM_BINS)
        counts[max(0, min(HISTOGRAM_BINS - 1, index))] += 1
    return counts


def _attempt_histogram(attempts: list[int]) -> list[dict[str, Any]]:
    """Build the stable, self-describing attempts-to-solve histogram."""
    counts = [0] * len(ATTEMPT_BINS)
    for attempt_count in attempts:
        for index, (_label, minimum, maximum) in enumerate(ATTEMPT_BINS):
            if attempt_count >= minimum and (maximum is None or attempt_count <= maximum):
                counts[index] += 1
                break
    return [
        {"label": label, "min": minimum, "max": maximum, "count": counts[index]}
        for index, (label, minimum, maximum) in enumerate(ATTEMPT_BINS)
    ]


def _solver_payload(solver: ProblemSolverRow) -> dict[str, str]:
    """Serialize a solver milestone for the JSON snapshot."""
    return {
        "user_id": solver.user_id,
        "name": solver.name,
        "solved_at": as_utc(solver.solved_at).isoformat(),
    }


def _solver_metrics(submissions: list[ProblemSubmissionRow], solvers: list[ProblemSolverRow]) -> dict[str, Any]:
    """Build solver milestones and attempts-to-solve aggregates.

    ``solvers`` is the authoritative population: ``solver_count`` and both
    milestones cover all of it. The attempts histogram and median cover only the
    solvers whose first-AC submission is identifiable, since an attempt count
    cannot be derived without one.
    """
    ordered_by_user: dict[str, list[ProblemSubmissionRow]] = defaultdict(list)
    for submission in submissions:
        ordered_by_user[submission.user_id].append(submission)

    positions: dict[str, dict[str, int]] = {}
    for user_id, rows in ordered_by_user.items():
        rows.sort(key=lambda row: (as_utc(row.created_at), row.submission_id))
        positions[user_id] = {row.submission_id: index for index, row in enumerate(rows, 1)}

    attempt_counts = [
        position
        for solver in solvers
        if solver.ac_submission_id is not None
        and (position := positions.get(solver.user_id, {}).get(solver.ac_submission_id)) is not None
    ]

    ordered_solvers = sorted(solvers, key=lambda solver: (as_utc(solver.solved_at), solver.user_id))
    last_timestamp = as_utc(ordered_solvers[-1].solved_at) if ordered_solvers else None
    last_solver = min(
        (solver for solver in ordered_solvers if as_utc(solver.solved_at) == last_timestamp),
        key=lambda solver: solver.user_id,
        default=None,
    )

    return {
        "first_solver": _solver_payload(ordered_solvers[0]) if ordered_solvers else None,
        "last_solver": _solver_payload(last_solver) if last_solver else None,
        "attempts_histogram": _attempt_histogram(attempt_counts),
        "median_attempts": float(statistics.median(attempt_counts)) if attempt_counts else None,
        "solver_count": len(solvers),
    }


def _submission_heatmap(submissions: list[ProblemSubmissionRow]) -> dict[str, Any]:
    """Build the sparse, chronological UTC submission heatmap."""
    daily_counts: dict[str, int] = defaultdict(int)
    for submission in submissions:
        daily_counts[utc_day(submission.created_at).isoformat()] += 1

    ordered_days = sorted(daily_counts.items())
    return {
        "first_date": ordered_days[0][0] if ordered_days else None,
        "last_date": ordered_days[-1][0] if ordered_days else None,
        "days": [[day, count] for day, count in ordered_days],
    }


def build_problem_statistics_payload(
    submissions: list[ProblemSubmissionRow],
    solvers: list[ProblemSolverRow],
    time_limit_ms: int,
    language_names: dict[str, str],
) -> dict[str, Any]:
    """Assemble the complete JSON-serializable snapshot for one problem."""
    verdict_counts: dict[str, int] = defaultdict(int)
    language_counts: dict[str, int] = defaultdict(int)
    accepted_wall_times: dict[str, list[int]] = defaultdict(list)
    accepted_memory: dict[str, list[int]] = defaultdict(list)

    for row in submissions:
        if row.verdict is None:
            continue
        verdict_counts[row.verdict] += 1
        language_counts[row.language_id] += 1
        if row.verdict == Verdict.AC.value:
            if row.wall_time_ms is not None:
                accepted_wall_times[row.language_id].append(row.wall_time_ms)
            if row.memory_kb is not None:
                accepted_memory[row.language_id].append(row.memory_kb)

    def language_name(language_id: str) -> str:
        return language_names.get(language_id, language_id)

    payload: dict[str, Any] = {
        "total_submissions": sum(verdict_counts.values()),
        "verdicts": [
            {"verdict": verdict, "count": count}
            for verdict, count in sorted(verdict_counts.items(), key=lambda item: item[1], reverse=True)
        ],
        "languages": [
            {"language_id": language_id, "name": language_name(language_id), "count": count}
            for language_id, count in sorted(language_counts.items(), key=lambda item: item[1], reverse=True)
        ],
        "time_stats": sorted(
            (
                {
                    "language_id": language_id,
                    "name": language_name(language_id),
                    "count": len(values),
                    "avg_ms": round(statistics.fmean(values), 1),
                    "stddev_ms": round(statistics.pstdev(values), 1) if len(values) > 1 else 0.0,
                }
                for language_id, values in accepted_wall_times.items()
            ),
            key=lambda row: cast(float, row["avg_ms"]),
        ),
        "memory_stats": sorted(
            (
                {
                    "language_id": language_id,
                    "name": language_name(language_id),
                    "count": len(values),
                    "avg_kb": round(statistics.fmean(values), 1),
                    "stddev_kb": round(statistics.pstdev(values), 1) if len(values) > 1 else 0.0,
                }
                for language_id, values in accepted_memory.items()
            ),
            key=lambda row: cast(float, row["avg_kb"]),
        ),
        "time_limit_ms": time_limit_ms,
        "histogram_bins": HISTOGRAM_BINS,
        "wall_time_histogram": [
            {
                "language_id": language_id,
                "name": language_name(language_id),
                "counts": _histogram_for(values, time_limit_ms),
            }
            for language_id, values in sorted(
                accepted_wall_times.items(), key=lambda item: language_name(item[0]).lower()
            )
        ],
    }
    payload.update(_solver_metrics(submissions, solvers))
    payload["submission_heatmap"] = _submission_heatmap(submissions)
    return payload
