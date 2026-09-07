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

import logging
from typing import cast

from sqlalchemy import delete, or_, select, true

from autojudge.db._base import PROFILING_DISPATCHABLE_STATUSES, AttemptClaim, _DatabaseBase, _utcnow
from autojudge.types import (
    JobNotDispatchable,
    JudgmentOwnershipLost,
    ProfilingObservedLimits,
    QueuedProfilingRun,
    RecoverableProfilingJob,
)
from shared.db_schema import problem_language_limits as _problem_language_limit
from shared.db_schema import problems as _problem
from shared.db_schema import profiling_case_results as _profiling_case_result
from shared.db_schema import profiling_runs as _profiling_run
from shared.enumerations import ProfilingStatus

logger = logging.getLogger(__name__)


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
            .where(_profiling_run.c.status.in_(PROFILING_DISPATCHABLE_STATUSES))
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

    async def set_profiling_dispatched(
        self,
        profiling_run_id: str,
        worker_id: str,
        attempt_token: str,
    ) -> None:
        """
        Mark a profiling run as dispatched and claim it for this attempt.

        Retry-safe: removes partial per-test-case profiling rows before restart.

        Fenced on a non-terminal status before the delete, so a dequeue racing
        with the run's own completion cannot reset it or drop its results. That
        fence is deliberately not a claim — taking a run over from a stalled
        attempt is what the reaper's requeue is for. ``attempt_token`` is the
        claim: stamping it here revokes any older attempt's right to write.

        Args:
            profiling_run_id: UUID of the profiling run.
            worker_id: Stable worker identity string.
            attempt_token: Attempt-scoped claim for this dispatch.

        Raises:
            LookupError: If the run already reached a terminal status.
        """
        now = _utcnow()
        result = await self._conn.execute(
            _profiling_run.update()
            .where(
                _profiling_run.c.id == profiling_run_id,
                _profiling_run.c.status.in_(PROFILING_DISPATCHABLE_STATUSES),
            )
            .values(
                status=ProfilingStatus.DISPATCHED,
                worker_id=worker_id,
                attempt_token=attempt_token,
                started_at=now,
                finished_at=None,
                compile_log=None,
                error_message=None,
                updated_at=now,
            )
        )
        if result.rowcount == 0:
            await self._conn.rollback()
            raise JobNotDispatchable(f"Profiling run {profiling_run_id} is no longer dispatchable")

        await self._conn.execute(
            delete(_profiling_case_result).where(_profiling_case_result.c.profiling_run_id == profiling_run_id)
        )
        await self._conn.commit()

    async def set_profiling_running(self, profiling_run_id: str, attempt_token: str, repetitions: int) -> None:
        """
        Mark a profiling run as actively executing test cases.

        The repetition count is frozen here, at the moment the run starts, so
        the stored per-run limit can always be reconciled with the observation
        it came from even after the language registry's default changes.

        Args:
            profiling_run_id: UUID of the profiling run.
            attempt_token: Claim stamped by this attempt's dispatch.
            repetitions: Repetitions this run will measure each test case across.

        Raises:
            JudgmentOwnershipLost: If another attempt has claimed the run.
        """
        result = await self._conn.execute(
            _profiling_run.update()
            .where(self._claim_predicate(AttemptClaim(_profiling_run, profiling_run_id, attempt_token)))
            .values(status=ProfilingStatus.RUNNING, repetitions=repetitions, updated_at=_utcnow())
        )
        if result.rowcount == 0:
            await self._conn.rollback()
            raise JudgmentOwnershipLost(f"Profiling run {profiling_run_id} was claimed by another attempt")
        await self._conn.commit()

    async def set_profiling_failed(
        self,
        profiling_run_id: str,
        error_message: str,
        *,
        compile_log: str | None = None,
        attempt_token: str | None = None,
    ) -> None:
        """
        Mark a profiling run as failed and persist diagnostics.

        Fenced on a non-terminal status, and on a claim that is either this
        attempt's or absent: a run claimed by another attempt is that attempt's
        to finish. A fenced-out call is a logged no-op.

        Args:
            profiling_run_id: UUID of the profiling run.
            error_message: Description of the failure.
            compile_log: Optional compiler output to persist.
            attempt_token: Claim stamped by this attempt's dispatch, when it got
                as far as dispatching.
        """
        now = _utcnow()
        claim_filter = (
            or_(
                _profiling_run.c.attempt_token.is_(None),
                _profiling_run.c.attempt_token == attempt_token,
            )
            if attempt_token is not None
            else true()
        )
        result = await self._conn.execute(
            _profiling_run.update()
            .where(
                _profiling_run.c.id == profiling_run_id,
                _profiling_run.c.status.in_(PROFILING_DISPATCHABLE_STATUSES),
                claim_filter,
            )
            .values(
                status=ProfilingStatus.FAILED,
                error_message=error_message,
                compile_log=compile_log,
                finished_at=now,
                updated_at=now,
            )
        )
        if result.rowcount == 0:
            logger.warning(
                "Profiling run %s is terminal or owned by another attempt; not marking it FAILED: %s",
                profiling_run_id,
                error_message,
            )
        await self._conn.commit()

    async def set_profiling_done(
        self,
        profiling_run_id: str,
        observed_limits: ProfilingObservedLimits,
        repetitions: int,
        *,
        attempt_token: str,
        compile_log: str | None = None,
    ) -> None:
        """
        Persist computed limits and mark a profiling run as done.

        Upserts a problem_language_limit row for the profiled language.

        Args:
            profiling_run_id: UUID of the profiling run.
            observed_limits: Observed resource peaks with safety factor applied.
            repetitions: Repetition count used for this run (persisted for auditing).
            attempt_token: Claim stamped by this attempt's dispatch.
            compile_log: Optional compiler output to persist.

        Raises:
            LookupError: If the profiling run row is missing.
            JudgmentOwnershipLost: If another attempt has claimed the run.
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

        # Claim first, in the same transaction as the limits below: a stale
        # attempt must not publish its profiled limits over the new owner's.
        claimed = await self._conn.execute(
            _profiling_run.update()
            .where(self._claim_predicate(AttemptClaim(_profiling_run, profiling_run_id, attempt_token)))
            .values(
                status=ProfilingStatus.DONE,
                compile_log=compile_log,
                error_message=None,
                finished_at=now,
                updated_at=now,
            )
        )
        if claimed.rowcount == 0:
            await self._conn.rollback()
            raise JudgmentOwnershipLost(f"Profiling run {profiling_run_id} was claimed by another attempt")

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

        await self._conn.commit()
