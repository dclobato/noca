#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
autojudge/db/_solution_test.py

Mixin for non-scoring solution-test run loading and state transitions.

The status machine mirrors ``submission_judgments`` so the worker's state
machine is reused verbatim, but nothing here touches contest standings: there
is no verdict publishing, no scoreboard invalidation, and no balloon task.
"""

from __future__ import annotations

import logging
import uuid
from typing import cast

from sqlalchemy import delete, or_, select, true, update

from autojudge.db._base import JUDGMENT_DISPATCHABLE_STATUSES, AttemptClaim, _DatabaseBase, _utcnow
from autojudge.runtime_utils import decode_for_text_column
from autojudge.types import (
    JobNotDispatchable,
    JudgmentOwnershipLost,
    QueuedSolutionTestRun,
    RecoverableSolutionTestJob,
)
from shared.db_schema import problems as _problem
from shared.db_schema import solution_test_case_results as _solution_test_case_result
from shared.db_schema import solution_test_runs as _solution_test_run
from shared.enumerations import JudgmentStatus, Verdict

_ACTIVE_STATUSES = JUDGMENT_DISPATCHABLE_STATUSES

logger = logging.getLogger(__name__)


class _SolutionTestMixin(_DatabaseBase):
    """Database operations for solution-test run loading and state transitions."""

    async def get_solution_test_run_for_judging(self, solution_test_run_id: str) -> QueuedSolutionTestRun:
        """
        Return the solution-test run payload needed by the worker.

        Args:
            solution_test_run_id: UUID of the solution-test run.

        Returns:
            QueuedSolutionTestRun with source code and configuration.

        Raises:
            LookupError: If the run is not found or is already terminal.
        """
        row = await self._conn.execute(
            select(
                _solution_test_run.c.id,
                _solution_test_run.c.problem_id,
                _solution_test_run.c.language_id,
                _solution_test_run.c.source_code,
                _solution_test_run.c.status,
                _problem.c.contest_id,
            )
            .select_from(_solution_test_run.join(_problem, _problem.c.id == _solution_test_run.c.problem_id))
            .where(_solution_test_run.c.id == solution_test_run_id)
        )
        result = row.mappings().first()
        if result is None:
            raise LookupError(f"Solution test run '{solution_test_run_id}' not found in database")

        status = cast(JudgmentStatus, result["status"])
        if status in {JudgmentStatus.DONE, JudgmentStatus.FAILED}:
            raise LookupError(f"Solution test run '{solution_test_run_id}' is not runnable (status={status})")

        return QueuedSolutionTestRun(
            solution_test_run_id=cast(str, result["id"]),
            contest_id=cast(str, result["contest_id"]),
            problem_id=cast(str, result["problem_id"]),
            language_id=cast(str, result["language_id"]),
            source_code=cast(str, result["source_code"]),
        )

    async def list_recoverable_solution_test_jobs(self) -> list[RecoverableSolutionTestJob]:
        """
        Return all non-terminal solution-test runs that can be re-enqueued.

        Returns:
            List of RecoverableSolutionTestJob ordered by created_at.
        """
        rows = await self._conn.execute(
            select(
                _solution_test_run.c.id,
                _solution_test_run.c.problem_id,
                _solution_test_run.c.language_id,
                _solution_test_run.c.source_code,
                _solution_test_run.c.status,
                _problem.c.contest_id,
            )
            .select_from(_solution_test_run.join(_problem, _problem.c.id == _solution_test_run.c.problem_id))
            .where(_solution_test_run.c.status.in_(_ACTIVE_STATUSES))
            .order_by(_solution_test_run.c.created_at)
        )
        return [
            RecoverableSolutionTestJob(
                status=cast(JudgmentStatus, row["status"]),
                payload=QueuedSolutionTestRun(
                    solution_test_run_id=cast(str, row["id"]),
                    contest_id=cast(str, row["contest_id"]),
                    problem_id=cast(str, row["problem_id"]),
                    language_id=cast(str, row["language_id"]),
                    source_code=cast(str, row["source_code"]),
                ),
            )
            for row in rows.mappings().all()
        ]

    async def set_solution_test_dispatched(
        self,
        solution_test_run_id: str,
        worker_id: str,
        attempt_token: str,
    ) -> None:
        """
        Mark a solution-test run as dispatched and claim it for this attempt.

        Retry-safe: removes partial per-test-case rows before restart, which is
        what keeps the two partial unique indexes satisfiable across retries.

        Fenced on a non-terminal status before the delete, so a dequeue racing
        with the run's own completion cannot reset it or drop its results. That
        fence is deliberately not a claim — taking a run over from a stalled
        attempt is what the reaper's requeue is for. ``attempt_token`` is the
        claim: stamping it here revokes any older attempt's right to write.

        Args:
            solution_test_run_id: UUID of the solution-test run.
            worker_id: Stable worker identity string.
            attempt_token: Attempt-scoped claim for this dispatch.

        Raises:
            LookupError: If the run already reached a terminal status.
        """
        now = _utcnow()
        result = await self._conn.execute(
            update(_solution_test_run)
            .where(
                _solution_test_run.c.id == solution_test_run_id,
                _solution_test_run.c.status.in_(_ACTIVE_STATUSES),
            )
            .values(
                status=JudgmentStatus.DISPATCHED,
                worker_id=worker_id,
                attempt_token=attempt_token,
                started_at=now,
                finished_at=None,
                verdict=None,
                compile_log=None,
                error_message=None,
                max_wall_time_ms=None,
                max_memory_kb=None,
                updated_at=now,
            )
        )
        if result.rowcount == 0:
            await self._conn.rollback()
            raise JobNotDispatchable(f"Solution-test run {solution_test_run_id} is no longer dispatchable")

        await self._conn.execute(
            delete(_solution_test_case_result).where(
                _solution_test_case_result.c.solution_test_run_id == solution_test_run_id
            )
        )
        await self._conn.commit()

    async def set_solution_test_running(self, solution_test_run_id: str, attempt_token: str) -> None:
        """
        Mark a solution-test run as actively executing test cases.

        Args:
            solution_test_run_id: UUID of the solution-test run.
            attempt_token: Claim stamped by this attempt's dispatch.

        Raises:
            JudgmentOwnershipLost: If another attempt has claimed the run.
        """
        result = await self._conn.execute(
            update(_solution_test_run)
            .where(self._claim_predicate(AttemptClaim(_solution_test_run, solution_test_run_id, attempt_token)))
            .values(status=JudgmentStatus.JUDGING, updated_at=_utcnow())
        )
        if result.rowcount == 0:
            await self._conn.rollback()
            raise JudgmentOwnershipLost(f"Solution-test run {solution_test_run_id} was claimed by another attempt")
        await self._conn.commit()

    async def set_solution_test_failed(
        self,
        solution_test_run_id: str,
        error_message: str,
        *,
        compile_log: str | None = None,
        attempt_token: str | None = None,
    ) -> None:
        """
        Mark a solution-test run as failed and persist diagnostics.

        Fenced on a non-terminal status, and on a claim that is either this
        attempt's or absent: a run claimed by another attempt is that attempt's
        to finish. A fenced-out call is a logged no-op.

        Args:
            solution_test_run_id: UUID of the solution-test run.
            error_message: Description of the failure.
            compile_log: Optional compiler output to persist.
            attempt_token: Claim stamped by this attempt's dispatch, when it got
                as far as dispatching.
        """
        # A prior statement may have left the transaction aborted; roll back so
        # the UPDATE below runs in a clean one and the run reaches a terminal state.
        await self._conn.rollback()
        now = _utcnow()
        claim_filter = (
            or_(
                _solution_test_run.c.attempt_token.is_(None),
                _solution_test_run.c.attempt_token == attempt_token,
            )
            if attempt_token is not None
            else true()
        )
        result = await self._conn.execute(
            update(_solution_test_run)
            .where(
                _solution_test_run.c.id == solution_test_run_id,
                _solution_test_run.c.status.in_(_ACTIVE_STATUSES),
                claim_filter,
            )
            .values(
                status=JudgmentStatus.FAILED,
                error_message=error_message,
                compile_log=compile_log,
                finished_at=now,
                updated_at=now,
            )
        )
        if result.rowcount == 0:
            logger.warning(
                "Solution-test run %s is terminal or owned by another attempt; not marking it FAILED: %s",
                solution_test_run_id,
                error_message,
            )
        await self._conn.commit()

    async def set_solution_test_done(
        self,
        solution_test_run_id: str,
        *,
        verdict: Verdict,
        attempt_token: str,
        compile_log: str | None = None,
        max_wall_time_ms: int | None = None,
        max_memory_kb: int | None = None,
    ) -> None:
        """
        Persist the final verdict and mark a solution-test run as done.

        Fenced on this attempt's claim, so a stalled attempt finishing late
        cannot overwrite the verdict its replacement produced.

        Args:
            solution_test_run_id: UUID of the solution-test run.
            verdict: Final verdict; never human-confirmed and never scored.
            attempt_token: Claim stamped by this attempt's dispatch.
            compile_log: Optional compiler output to persist.
            max_wall_time_ms: Peak wall time across executed cases.
            max_memory_kb: Peak memory across executed cases.

        Raises:
            JudgmentOwnershipLost: If another attempt has claimed the run.
        """
        now = _utcnow()
        result = await self._conn.execute(
            update(_solution_test_run)
            .where(self._claim_predicate(AttemptClaim(_solution_test_run, solution_test_run_id, attempt_token)))
            .values(
                status=JudgmentStatus.DONE,
                verdict=verdict,
                compile_log=compile_log,
                error_message=None,
                max_wall_time_ms=max_wall_time_ms,
                max_memory_kb=max_memory_kb,
                finished_at=now,
                updated_at=now,
            )
        )
        if result.rowcount == 0:
            await self._conn.rollback()
            raise JudgmentOwnershipLost(f"Solution-test run {solution_test_run_id} was claimed by another attempt")
        await self._conn.commit()

    async def fail_exhausted_solution_test_run(self, solution_test_run_id: str) -> bool:
        """Terminalize a run whose queue job the reaper dropped as a poison pill.

        Without this the reaper's tombstoned hash and the still non-terminal row
        would make the reconciler re-enqueue the run forever.

        Args:
            solution_test_run_id: UUID of the solution-test run.

        Returns:
            Whether a non-terminal row was moved to FAILED.
        """
        now = _utcnow()
        result = await self._conn.execute(
            update(_solution_test_run)
            .where(
                _solution_test_run.c.id == solution_test_run_id,
                _solution_test_run.c.status.in_(_ACTIVE_STATUSES),
            )
            .values(
                status=JudgmentStatus.FAILED,
                error_message=(
                    "The judge could not complete this solution test after repeated attempts. "
                    "Submit it again once the underlying issue is resolved."
                ),
                finished_at=now,
                updated_at=now,
            )
        )
        await self._conn.commit()
        return bool(result.rowcount)

    async def insert_solution_test_case_result(
        self,
        *,
        solution_test_run_id: str,
        test_case_id: str | None,
        ordinal: int,
        verdict: Verdict,
        wall_time_ms: int | None = None,
        memory_kb: int | None = None,
        output_bytes: int | None = None,
        exit_code: int | None = None,
        exit_signal: int | None = None,
        input_excerpt: bytes = b"",
        expected_output_excerpt: bytes = b"",
        stdout_excerpt: bytes = b"",
        stderr_excerpt: bytes = b"",
        attempt_token: str | None = None,
    ) -> None:
        """Persist one ordinary (non-interactive) test-case result.

        Written only while this attempt still holds the run's claim: a stale
        attempt's late row would interleave with the new owner's results rather
        than collide with them.

        Args:
            solution_test_run_id: UUID of the owning run.
            test_case_id: UUID of the executed test case, when it still exists.
            ordinal: 1-based test-case ordinal.
            verdict: Verdict for this case.
            wall_time_ms: Total wall time across repetitions.
            memory_kb: Peak memory.
            output_bytes: Peak stdout size.
            exit_code: Contestant exit code.
            exit_signal: Fatal signal number reported by isolate.
            input_excerpt: Bounded snapshot of the executed problem input.
            expected_output_excerpt: Bounded snapshot of the expected output.
            stdout_excerpt: Bounded stdout captured by the runner.
            stderr_excerpt: Bounded stderr captured by the runner.
            attempt_token: Claim stamped by this attempt's dispatch.

        Raises:
            JudgmentOwnershipLost: If another attempt has claimed the run.
        """
        await self._insert_claimed_row(
            _solution_test_case_result,
            values={
                "id": str(uuid.uuid4()),
                "solution_test_run_id": solution_test_run_id,
                "test_case_id": test_case_id,
                "ordinal": ordinal,
                "attempt_number": None,
                "verdict": verdict,
                "wall_time_ms": wall_time_ms,
                "memory_kb": memory_kb,
                "output_bytes": output_bytes,
                "exit_code": exit_code,
                "exit_signal": exit_signal,
                "input_excerpt": decode_for_text_column(input_excerpt),
                "expected_output_excerpt": decode_for_text_column(expected_output_excerpt),
                "stdout_excerpt": decode_for_text_column(stdout_excerpt),
                "stderr_excerpt": decode_for_text_column(stderr_excerpt),
                "transcript": None,
                "created_at": _utcnow(),
            },
            claim=(
                AttemptClaim(_solution_test_run, solution_test_run_id, attempt_token)
                if attempt_token is not None
                else None
            ),
        )
        await self._conn.commit()
