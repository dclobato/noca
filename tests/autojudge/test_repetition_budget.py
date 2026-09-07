#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""What a stored time limit buys once repetitions are involved.

``time_limit_ms`` is the time for one run of a test case. The budget for the
whole case is that limit times the repetition count, and it is *spent* rather
than re-imposed on each run -- so the verdict follows the total, and a single
slow repetition is covered by faster ones.
"""

from __future__ import annotations

from collections.abc import Sequence
from unittest.mock import AsyncMock

import pytest

from autojudge.submission_job import _run_repeated_test_case
from autojudge.types import ProblemLimits, RunResult, case_budget_ms
from shared.enumerations import Verdict


def _limits(time_limit_ms: int, repetitions: int) -> ProblemLimits:
    return ProblemLimits(
        time_limit_ms=time_limit_ms,
        memory_limit_kb=65536,
        pids_limit=16,
        output_limit_in_bytes=4096,
        repetitions=repetitions,
    )


def _fake_runner(wall_times: Sequence[int]) -> AsyncMock:
    """A stand-in for run_test_case that enforces the ceiling it is handed.

    That is what isolate does for real, and modelling it is the point: a run
    only reports the time it wanted if the remaining budget covers it, and
    otherwise comes back TLE having burned what was left.
    """
    remaining = list(wall_times)

    async def _run_one(**kwargs: object) -> RunResult:
        ceiling = kwargs["limits"].time_limit_ms  # type: ignore[union-attr]
        wanted = remaining.pop(0)
        if wanted > ceiling:
            return RunResult(verdict=Verdict.TLE, wall_time_ms=ceiling, memory_kb=1024)
        return RunResult(verdict=Verdict.AC, wall_time_ms=wanted, memory_kb=1024)

    return AsyncMock(side_effect=_run_one)


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    limits: ProblemLimits,
    wall_times: Sequence[int],
    per_run_ceiling_ms: int | None = None,
) -> tuple:
    """Run one test case with a stubbed runner; return the result and the ceilings it saw."""
    runner = _fake_runner(wall_times)
    monkeypatch.setattr("autojudge.submission_job.run_test_case", runner)
    result = await _run_repeated_test_case(
        container_id="container",
        language=AsyncMock(),
        limits=limits,
        artifact_data=b"artifact",
        input_data=b"1 2\n",
        expected_output=b"3\n",
        docker_client=AsyncMock(),
        executor=AsyncMock(),
        per_run_ceiling_ms=per_run_ceiling_ms,
    )
    ceilings = [call.kwargs["limits"].time_limit_ms for call in runner.await_args_list]
    return result, ceilings


def test_case_budget_is_the_per_run_limit_times_the_repetitions() -> None:
    assert case_budget_ms(_limits(500, 4)) == 2000
    # A problem with no per-language row runs each case once, so the two coincide.
    assert case_budget_ms(_limits(500, 1)) == 500
    # A malformed zero can never widen the budget to nothing.
    assert case_budget_ms(_limits(500, 0)) == 500


@pytest.mark.asyncio
async def test_four_runs_at_the_limit_spend_the_budget_exactly_and_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """500 ms x 4 repetitions is a 2000 ms budget, and four 500 ms runs fit in it."""
    result, ceilings = await _run(monkeypatch, _limits(500, 4), [500, 500, 500, 500])

    assert result.verdict == Verdict.AC
    assert result.total_wall_time_ms == 2000
    # The first repetition is handed the whole budget, then each later one gets
    # what is left: the budget is shared, not re-imposed per run.
    assert ceilings == [2000, 1500, 1000, 500]


@pytest.mark.asyncio
async def test_consistently_over_the_limit_exhausts_the_budget_and_tles(monkeypatch: pytest.MonkeyPatch) -> None:
    """600 ms runs against a 500 ms limit overrun the 2000 ms budget.

    Three runs spend 1800 ms, so the fourth is launched with the 200 ms that is
    left and is cut off there.
    """
    result, ceilings = await _run(monkeypatch, _limits(500, 4), [600, 600, 600, 600])

    assert result.verdict == Verdict.TLE
    assert ceilings == [2000, 1400, 800, 200]


@pytest.mark.asyncio
async def test_one_slow_repetition_borrows_from_the_faster_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    """TLE means the *average* run went over, not that a single unlucky one did.

    This is the property that makes repetitions smooth measurement noise. Under
    a per-run ceiling the 900 ms run below would fail on its own, handing every
    submission N independent chances to get unlucky.
    """
    result, _ = await _run(monkeypatch, _limits(500, 4), [900, 300, 300, 400])

    assert result.verdict == Verdict.AC
    assert result.total_wall_time_ms == 1900


@pytest.mark.asyncio
async def test_a_per_run_ceiling_bounds_one_execution_without_shrinking_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Profiling needs both constraints, and they are different numbers.

    Its cap is not a problem's time limit -- it is an infrastructure ceiling
    (`NOCA_JUDGE_PROFILING_MAX_CPU_TIME_SEC`) whose job is to bound a single
    execution. Without the ceiling, a 30 s cap over 10 repetitions would hand
    the first run all 300 s, so one hung reference implementation could hold a
    judge container for the whole aggregate.
    """
    cap_ms = 30_000
    limits = _limits(cap_ms, 10)

    result, ceilings = await _run(
        monkeypatch,
        limits,
        [1000] * 10,
        per_run_ceiling_ms=cap_ms,
    )

    assert result.verdict == Verdict.AC
    # Every execution is capped at the cap, never at what is left of the aggregate.
    assert ceilings == [cap_ms] * 10
    # And the aggregate is still cap x repetitions, so a slow-but-valid
    # reference implementation is not squeezed into one cap's worth.
    assert case_budget_ms(limits) == 300_000


@pytest.mark.asyncio
async def test_whichever_of_the_two_bounds_is_tighter_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both constraints hold at once: a run gets the smaller of the two.

    A ceiling above the aggregate must not widen the case, so the run is still
    handed only what is left of the budget.
    """
    result, ceilings = await _run(
        monkeypatch,
        _limits(500, 2),  # a 1000 ms aggregate
        [400, 400],
        per_run_ceiling_ms=4000,  # deliberately looser than the whole case
    )

    assert result.verdict == Verdict.AC
    # 1000 ms of budget, not the 4000 ms ceiling; then 600 ms of it left.
    assert ceilings == [1000, 600]


@pytest.mark.asyncio
async def test_judging_a_submission_sets_no_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Borrowing is the design for a problem's own time limit, so nothing caps it."""
    _, ceilings = await _run(monkeypatch, _limits(500, 4), [500, 500, 500, 500])

    assert ceilings[0] == 2000
