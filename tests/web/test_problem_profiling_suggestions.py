#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The Auto-Limit suggestion the Limits tab recomputes.

An Auto-Limit figure is produced twice: the worker persists it, and the Web
layer recomputes it to decide whether a stored row still matches. If the two
ever disagree, every freshly auto-limited language renders as a manual edit --
which is exactly what happened while each owned its own copy of the formula.
"""

from __future__ import annotations

from autojudge.db._results import _ResultsMixin
from shared.enumerations import ProfilingStatus, Verdict
from web.models.problem import ProfilingCaseResult, ProfilingRun
from web.services.problem_service.profiling import compute_profiling_limits_map


def _run(*, repetitions: int, safety_factor: float, case_wall_times: list[int]) -> ProfilingRun:
    """Build an unattached DONE profiling run carrying the given case results."""
    run = ProfilingRun(
        problem_id="problem-1",
        language_id="python3",
        source_code="print(1)",
        source_hash="hash",
        status=ProfilingStatus.DONE,
        safety_factor=safety_factor,
        repetitions=repetitions,
    )
    run.case_results = [
        ProfilingCaseResult(
            test_case_id=f"case-{ordinal}",
            ordinal=ordinal,
            total_wall_time_ms=wall_time,
            peak_memory_kb=2048,
            peak_output_bytes=64,
            peak_pids=4,
            verdict=Verdict.AC,
        )
        for ordinal, wall_time in enumerate(case_wall_times, start=1)
    ]
    return run


def test_the_suggested_time_limit_is_the_mean_of_one_run() -> None:
    """Case wall times are sums across repetitions; the stored limit is not."""
    suggestions = compute_profiling_limits_map([_run(repetitions=3, safety_factor=1.5, case_wall_times=[1200, 3000])])

    # The slowest case still sets the limit: 3000 ms over three runs, x1.5.
    assert suggestions["python3"]["time_limit_ms"] == 1500


def test_the_suggestion_matches_what_the_worker_would_store() -> None:
    """The whole point of sharing the formula, asserted directly.

    A mismatch here is not a rounding curiosity: the Limits tab compares the
    suggestion against the stored row to label it auto or manual, so a
    disagreement of one millisecond mislabels every auto-limited language.
    """
    run = _run(repetitions=3, safety_factor=1.5, case_wall_times=[1000])
    observed_total = max(case.total_wall_time_ms or 0 for case in run.case_results)

    worker_limits = _ResultsMixin.compute_profiled_limits(
        safety_factor=run.safety_factor,
        time_limit_ms=observed_total,
        repetitions=run.repetitions,
        memory_limit_kb=2048,
        pids_limit=4,
        output_limit_in_bytes=64,
    )
    suggestions = compute_profiling_limits_map([run])

    assert suggestions["python3"]["time_limit_ms"] == worker_limits.time_limit_ms
    # And it rounds once: ceil(1.5 * ceil(1000 / 3)) would be 501.
    assert worker_limits.time_limit_ms == 500


def test_a_single_repetition_leaves_the_observation_undivided() -> None:
    suggestions = compute_profiling_limits_map([_run(repetitions=1, safety_factor=2.0, case_wall_times=[750])])

    assert suggestions["python3"]["time_limit_ms"] == 1500


def test_peaks_are_not_divided_by_the_repetition_count() -> None:
    """Memory, PIDs and output are peaks across the runs, not sums of them."""
    suggestions = compute_profiling_limits_map([_run(repetitions=10, safety_factor=1.0, case_wall_times=[500])])

    assert suggestions["python3"]["memory_limit_kb"] == 2048
    assert suggestions["python3"]["output_limit_in_bytes"] == 64
