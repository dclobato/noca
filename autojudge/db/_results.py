#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
autojudge/db/_results.py

Mixin for persisting per-test-case execution results and computing
safety-factor-adjusted profiling limits.
"""

from __future__ import annotations

import math
import uuid

from autojudge.db._base import _DatabaseBase, _utcnow
from autojudge.types import ProfilingObservedLimits
from shared.db_schema import profiling_case_results as _profiling_case_result
from shared.db_schema import submission_test_results as _submission_test_result
from shared.enumerations import Verdict


class _ResultsMixin(_DatabaseBase):
    """Database operations for test result and profiling result persistence."""

    async def insert_test_result(
        self,
        judgment_id: str,
        test_case_id: str,
        verdict: Verdict,
        wall_time_ms: int | None,
        memory_kb: int | None,
        exit_code: int | None,
        stdout_excerpt: bytes,
        stderr_excerpt: bytes,
    ) -> None:
        """
        Persist the result of running one test case.

        Args:
            judgment_id: UUID of the parent judgment.
            test_case_id: UUID of the test case.
            verdict: Verdict for this test case.
            wall_time_ms: Measured wall-clock time in milliseconds.
            memory_kb: Peak memory usage in kilobytes.
            exit_code: Process exit code.
            stdout_excerpt: First N bytes of stdout (already truncated).
            stderr_excerpt: First N bytes of stderr (already truncated).
        """
        await self._conn.execute(
            _submission_test_result.insert().values(
                id=str(uuid.uuid4()),
                judgment_id=judgment_id,
                test_case_id=test_case_id,
                verdict=verdict,
                wall_time_ms=wall_time_ms,
                memory_kb=memory_kb,
                exit_code=exit_code,
                stdout_excerpt=stdout_excerpt.decode(errors="replace"),
                stderr_excerpt=stderr_excerpt.decode(errors="replace"),
                created_at=_utcnow(),
            )
        )
        await self._conn.commit()

    async def insert_profiling_case_result(
        self,
        profiling_run_id: str,
        test_case_id: str,
        ordinal: int,
        verdict: Verdict,
        total_wall_time_ms: int | None,
        peak_memory_kb: int | None,
        peak_output_bytes: int | None,
        peak_pids: int | None,
        exit_code: int | None,
    ) -> None:
        """
        Persist the aggregated result of profiling one test case.

        Args:
            profiling_run_id: UUID of the parent profiling run.
            test_case_id: UUID of the test case.
            ordinal: 1-based test case ordinal.
            verdict: Verdict from the profiling execution.
            total_wall_time_ms: Sum of wall time across repetitions.
            peak_memory_kb: Peak memory across repetitions.
            peak_output_bytes: Peak output size across repetitions.
            peak_pids: Peak PID count across repetitions.
            exit_code: Process exit code.
        """
        await self._conn.execute(
            _profiling_case_result.insert().values(
                id=str(uuid.uuid4()),
                profiling_run_id=profiling_run_id,
                test_case_id=test_case_id,
                ordinal=ordinal,
                total_wall_time_ms=total_wall_time_ms,
                peak_memory_kb=peak_memory_kb,
                peak_output_bytes=peak_output_bytes,
                peak_pids=peak_pids,
                verdict=verdict,
                exit_code=exit_code,
                created_at=_utcnow(),
            )
        )
        await self._conn.commit()

    @staticmethod
    def compute_profiled_limits(
        *,
        safety_factor: float,
        time_limit_ms: int,
        memory_limit_kb: int,
        pids_limit: int,
        output_limit_in_bytes: int,
        pids_floor: int = 1,
    ) -> ProfilingObservedLimits:
        """
        Apply the safety factor with ceil rounding to all observed peaks.

        Args:
            safety_factor: Multiplier applied to all resource peaks.
            time_limit_ms: Observed peak wall time in milliseconds.
            memory_limit_kb: Observed peak memory in kilobytes.
            pids_limit: Observed peak PID count.
            output_limit_in_bytes: Observed peak output size in bytes.
            pids_floor: Minimum allowed pids_limit after scaling.

        Returns:
            ProfilingObservedLimits with safety-factor-adjusted values.
        """
        return ProfilingObservedLimits(
            time_limit_ms=max(1, math.ceil(safety_factor * time_limit_ms)),
            memory_limit_kb=max(1, math.ceil(safety_factor * memory_limit_kb)),
            pids_limit=max(pids_floor, math.ceil(safety_factor * pids_limit)),
            output_limit_in_bytes=max(1, math.ceil(safety_factor * output_limit_in_bytes)),
        )
