#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
autojudge/db/_profiling.py

Mixin for profiling run data loading and state machine transitions.
"""

from __future__ import annotations

from typing import cast

from sqlalchemy import delete, select

from autojudge.db._base import _DatabaseBase, _utcnow
from autojudge.types import ProfilingObservedLimits, QueuedProfilingRun, RecoverableProfilingJob
from shared.db_schema import problem_language_limits as _problem_language_limit
from shared.db_schema import problems as _problem
from shared.db_schema import profiling_case_results as _profiling_case_result
from shared.db_schema import profiling_runs as _profiling_run
from shared.enumerations import ProfilingStatus


class _ProfilingMixin(_DatabaseBase):
    """Database operations for profiling run loading and state transitions."""

    async def get_profiling_run_for_judging(self, profiling_run_id: str) -> QueuedProfilingRun:
        """
        Return the profiling run payload needed by the worker.

        Args:
            profiling_run_id: UUID of the profiling run.

        Returns:
            QueuedProfilingRun with source code and configuration.

        Raises:
            LookupError: If the run is not found or is already terminal.
        """
        row = await self._conn.execute(
            select(
                _profiling_run.c.id,
                _profiling_run.c.problem_id,
                _profiling_run.c.language_id,
                _profiling_run.c.source_code,
                _profiling_run.c.safety_factor,
                _profiling_run.c.status,
                _problem.c.contest_id,
            )
            .select_from(_profiling_run.join(_problem, _problem.c.id == _profiling_run.c.problem_id))
            .where(_profiling_run.c.id == profiling_run_id)
        )
        result = row.mappings().first()
        if result is None:
            raise LookupError(f"Profiling run '{profiling_run_id}' not found in database")

        status = cast(ProfilingStatus, result["status"])
        if status in {ProfilingStatus.DONE, ProfilingStatus.FAILED}:
            raise LookupError(f"Profiling run '{profiling_run_id}' is not runnable (status={status})")

        return QueuedProfilingRun(
            profiling_run_id=cast(str, result["id"]),
            contest_id=cast(str, result["contest_id"]),
            problem_id=cast(str, result["problem_id"]),
            language_id=cast(str, result["language_id"]),
            source_code=cast(str, result["source_code"]),
            safety_factor=cast(float, result["safety_factor"]),
        )

    async def list_recoverable_profiling_jobs(self) -> list[RecoverableProfilingJob]:
        """
        Return all non-terminal profiling runs that can be re-enqueued.

        Returns:
            List of RecoverableProfilingJob ordered by created_at.
        """
        rows = await self._conn.execute(
            select(
                _profiling_run.c.id,
                _profiling_run.c.problem_id,
                _profiling_run.c.language_id,
                _profiling_run.c.source_code,
                _profiling_run.c.safety_factor,
                _profiling_run.c.status,
                _problem.c.contest_id,
            )
            .select_from(_profiling_run.join(_problem, _problem.c.id == _profiling_run.c.problem_id))
            .where(
                _profiling_run.c.status.in_(
                    (ProfilingStatus.QUEUED, ProfilingStatus.DISPATCHED, ProfilingStatus.RUNNING)
                )
            )
            .order_by(_profiling_run.c.created_at)
        )
        result: list[RecoverableProfilingJob] = []
        for row in rows.mappings().all():
            result.append(
                RecoverableProfilingJob(
                    status=cast(ProfilingStatus, row["status"]),
                    payload=QueuedProfilingRun(
                        profiling_run_id=cast(str, row["id"]),
                        contest_id=cast(str, row["contest_id"]),
                        problem_id=cast(str, row["problem_id"]),
                        language_id=cast(str, row["language_id"]),
                        source_code=cast(str, row["source_code"]),
                        safety_factor=cast(float, row["safety_factor"]),
                    ),
                )
            )
        return result

    async def set_profiling_dispatched(self, profiling_run_id: str, worker_id: str) -> None:
        """
        Mark a profiling run as dispatched to a worker.

        Retry-safe: removes partial per-test-case profiling rows before restart.

        Args:
            profiling_run_id: UUID of the profiling run.
            worker_id: Stable worker identity string.
        """
        now = _utcnow()
        await self._conn.execute(
            delete(_profiling_case_result).where(_profiling_case_result.c.profiling_run_id == profiling_run_id)
        )
        await self._conn.execute(
            _profiling_run.update()
            .where(_profiling_run.c.id == profiling_run_id)
            .values(
                status=ProfilingStatus.DISPATCHED,
                worker_id=worker_id,
                started_at=now,
                finished_at=None,
                compile_log=None,
                error_message=None,
                updated_at=now,
            )
        )
        await self._conn.commit()

    async def set_profiling_running(self, profiling_run_id: str) -> None:
        """
        Mark a profiling run as actively executing test cases.

        Args:
            profiling_run_id: UUID of the profiling run.
        """
        await self._conn.execute(
            _profiling_run.update()
            .where(_profiling_run.c.id == profiling_run_id)
            .values(status=ProfilingStatus.RUNNING, updated_at=_utcnow())
        )
        await self._conn.commit()

    async def set_profiling_failed(
        self,
        profiling_run_id: str,
        error_message: str,
        *,
        compile_log: str | None = None,
    ) -> None:
        """
        Mark a profiling run as failed and persist diagnostics.

        Args:
            profiling_run_id: UUID of the profiling run.
            error_message: Description of the failure.
            compile_log: Optional compiler output to persist.
        """
        now = _utcnow()
        await self._conn.execute(
            _profiling_run.update()
            .where(_profiling_run.c.id == profiling_run_id)
            .values(
                status=ProfilingStatus.FAILED,
                error_message=error_message,
                compile_log=compile_log,
                finished_at=now,
                updated_at=now,
            )
        )
        await self._conn.commit()

    async def set_profiling_done(
        self,
        profiling_run_id: str,
        observed_limits: ProfilingObservedLimits,
        repetitions: int,
        *,
        compile_log: str | None = None,
    ) -> None:
        """
        Persist computed limits and mark a profiling run as done.

        Upserts a problem_language_limit row for the profiled language.

        Args:
            profiling_run_id: UUID of the profiling run.
            observed_limits: Observed resource peaks with safety factor applied.
            repetitions: Repetition count used for this run (persisted for auditing).
            compile_log: Optional compiler output to persist.

        Raises:
            LookupError: If the profiling run row is missing.
        """
        now = _utcnow()
        row = await self._conn.execute(
            select(_profiling_run.c.problem_id, _profiling_run.c.language_id).where(
                _profiling_run.c.id == profiling_run_id
            )
        )
        result = row.mappings().first()
        if result is None:
            raise LookupError(f"Profiling run '{profiling_run_id}' not found in database")

        existing = await self._conn.execute(
            select(_problem_language_limit.c.problem_id).where(
                _problem_language_limit.c.problem_id == result["problem_id"],
                _problem_language_limit.c.language_id == result["language_id"],
            )
        )
        limit_values = {
            "time_limit_ms": observed_limits.time_limit_ms,
            "memory_limit_kb": observed_limits.memory_limit_kb,
            "pids_limit": observed_limits.pids_limit,
            "output_limit_in_bytes": observed_limits.output_limit_in_bytes,
            "repetitions": repetitions,
            "updated_at": now,
        }

        if existing.first() is None:
            await self._conn.execute(
                _problem_language_limit.insert().values(
                    problem_id=result["problem_id"],
                    language_id=result["language_id"],
                    created_at=now,
                    **limit_values,
                )
            )
        else:
            await self._conn.execute(
                _problem_language_limit.update()
                .where(
                    _problem_language_limit.c.problem_id == result["problem_id"],
                    _problem_language_limit.c.language_id == result["language_id"],
                )
                .values(**limit_values)
            )

        await self._conn.execute(
            _profiling_run.update()
            .where(_profiling_run.c.id == profiling_run_id)
            .values(
                status=ProfilingStatus.DONE,
                compile_log=compile_log,
                error_message=None,
                finished_at=now,
                updated_at=now,
            )
        )
        await self._conn.commit()
