#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Database operations for Arena submission judging."""

from __future__ import annotations

import logging
import uuid
from typing import cast

from sqlalchemy import delete, or_, select, true, update

from autojudge.db._arena_solver import _ArenaSolverMixin
from autojudge.db._base import (
    JUDGMENT_DISPATCHABLE_STATUSES,
    RESULT_VOLATILE_COLUMNS,
    AttemptClaim,
    _utcnow,
)
from autojudge.runtime_utils import decode_for_text_column
from autojudge.types import (
    ArenaQueuedTestCase,
    JobNotDispatchable,
    JudgmentOwnershipLost,
    ProblemLimits,
    QueuedArenaSubmission,
    RecoverableArenaSubmissionJob,
)
from shared.db_schema.arena import arena_problems as _arena_problem
from shared.db_schema.arena import arena_submission_interactive_attempts as _arena_submission_interactive_attempt
from shared.db_schema.arena import arena_submission_judgments as _arena_submission_judgment
from shared.db_schema.arena import arena_submission_test_results as _arena_submission_test_result
from shared.db_schema.arena import arena_submissions as _arena_submission
from shared.db_schema.arena import arena_test_cases as _arena_test_case
from shared.enumerations import ArenaNotificationKind, JudgmentStatus, ProblemValidatorType, Verdict
from shared.services.arena_notification_service import create_arena_notification
from shared.services.testcase_files import get_testcase_path
from shared.tc_zip import normalize_testcase_bytes

# Maximum bytes read from a single Arena test case file (input or expected output).
_ARENA_TEST_FILE_MAX_BYTES = 256 * 1024 * 1024  # 256 MB

#: Statuses an Arena judgment may still be dispatched from (stored as strings).
_DISPATCHABLE_STATUSES = (*(status.value for status in JUDGMENT_DISPATCHABLE_STATUSES),)

logger = logging.getLogger(__name__)


class _ArenaSubmissionMixin(_ArenaSolverMixin):
    """Worker-side Arena submission database operations."""

    async def get_arena_submission_for_judging(self, judgment_id: str) -> QueuedArenaSubmission:
        """Load the full Arena submission payload for a pending judgment."""
        row = await self._conn.execute(
            select(
                _arena_submission_judgment.c.id.label("judgment_id"),
                _arena_submission_judgment.c.status,
                _arena_submission.c.id.label("submission_id"),
                _arena_submission.c.user_id,
                _arena_submission.c.problem_id,
                _arena_problem.c.arena_number.label("problem_number"),
                _arena_problem.c.title.label("problem_title"),
                _arena_submission.c.language_id,
                _arena_submission.c.source_code,
                _arena_problem.c.time_limit_ms,
                _arena_problem.c.memory_limit_kb,
                _arena_problem.c.pids_limit,
                _arena_problem.c.output_limit_in_bytes,
            )
            .select_from(
                _arena_submission_judgment.join(
                    _arena_submission,
                    _arena_submission.c.id == _arena_submission_judgment.c.submission_id,
                ).join(_arena_problem, _arena_problem.c.id == _arena_submission.c.problem_id)
            )
            .where(_arena_submission_judgment.c.id == judgment_id)
        )
        result = row.mappings().first()
        if result is None:
            raise LookupError(f"Arena judgment '{judgment_id}' not found in database")

        status = str(result["status"])
        if status in {JudgmentStatus.DONE.value, JudgmentStatus.FAILED.value, JudgmentStatus.SUPERSEDED.value}:
            raise LookupError(f"Arena judgment '{judgment_id}' is not judgeable (status={status})")

        problem_id = cast(str, result["problem_id"])
        strategy = await self._conn.scalar(
            select(_arena_problem.c.validator_type).where(_arena_problem.c.id == problem_id)
        )
        # An interactive problem's cases carry input only: the input parametrizes
        # the validator, which decides the verdict instead of an expected-output
        # file. This follows the stored strategy, so a stale validator row cannot
        # make a standard problem's cases look output-less and a removed source
        # cannot make an interactive problem's cases look like they need output.
        interactive = strategy == ProblemValidatorType.INTERACTIVE
        test_cases = await self._get_arena_test_cases(
            problem_id,
            require_expected_output=not interactive,
        )
        if not test_cases:
            raise LookupError(f"Arena problem '{result['problem_id']}' has no test cases")

        return QueuedArenaSubmission(
            judgment_id=cast(str, result["judgment_id"]),
            submission_id=cast(str, result["submission_id"]),
            user_id=cast(str, result["user_id"]),
            problem_id=cast(str, result["problem_id"]),
            problem_number=cast(int, result["problem_number"]),
            problem_title=cast(str, result["problem_title"]),
            language_id=cast(str, result["language_id"]),
            source_code=cast(str, result["source_code"]),
            limits=ProblemLimits(
                time_limit_ms=cast(int, result["time_limit_ms"]),
                memory_limit_kb=cast(int, result["memory_limit_kb"]),
                pids_limit=cast(int, result["pids_limit"]),
                output_limit_in_bytes=cast(int, result["output_limit_in_bytes"]),
            ),
            test_cases=tuple(test_cases),
        )

    async def list_recoverable_arena_submission_jobs(self) -> list[RecoverableArenaSubmissionJob]:
        """Return non-terminal Arena submission judgments that can be re-enqueued."""
        rows = await self._conn.execute(
            select(_arena_submission_judgment.c.id, _arena_submission_judgment.c.status)
            .where(_arena_submission_judgment.c.status.in_(_DISPATCHABLE_STATUSES))
            .order_by(_arena_submission_judgment.c.created_at)
        )

        result: list[RecoverableArenaSubmissionJob] = []
        for row in rows.mappings().all():
            payload = await self.get_arena_submission_for_judging(cast(str, row["id"]))
            result.append(
                RecoverableArenaSubmissionJob(
                    status=JudgmentStatus(cast(str, row["status"])),
                    payload=payload,
                )
            )
        return result

    async def set_arena_judgment_dispatched(
        self,
        judgment_id: str,
        worker_id: str,
        attempt_token: str,
    ) -> None:
        """Mark an Arena judgment as DISPATCHED and claim it for this attempt.

        Fenced on a non-terminal status before anything is deleted, so a dequeue
        racing with the judgment's own completion cannot reset the row or drop
        its finished results.

        The status fence is deliberately not a claim — ``DISPATCHED``/``JUDGING``
        are accepted, because taking a judgment over from a stalled attempt is
        exactly what the reaper's requeue is for. ``attempt_token`` is the claim:
        stamping it here revokes any older attempt's right to write, since every
        later write of an attempt is fenced on the token it stamped.

        Args:
            judgment_id: UUID of the Arena judgment.
            worker_id: Stable worker identity string.
            attempt_token: Attempt-scoped claim for this dispatch.

        Raises:
            LookupError: If the judgment already reached a terminal status.
        """
        now = _utcnow()
        result = await self._conn.execute(
            update(_arena_submission_judgment)
            .where(
                _arena_submission_judgment.c.id == judgment_id,
                _arena_submission_judgment.c.status.in_(_DISPATCHABLE_STATUSES),
            )
            .values(
                status=JudgmentStatus.DISPATCHED.value,
                worker_id=worker_id,
                attempt_token=attempt_token,
                started_at=now,
                finished_at=None,
                autojudge_verdict=None,
                final_verdict=None,
                compile_log=None,
                max_wall_time_ms=None,
                max_memory_kb=None,
                error_message=None,
            )
        )
        if result.rowcount == 0:
            await self._conn.rollback()
            raise JobNotDispatchable(f"Arena judgment {judgment_id} is no longer dispatchable")

        await self._conn.execute(
            delete(_arena_submission_test_result).where(
                _arena_submission_test_result.c.judgment_id == judgment_id,
            )
        )
        await self._conn.execute(
            delete(_arena_submission_interactive_attempt).where(
                _arena_submission_interactive_attempt.c.judgment_id == judgment_id,
            )
        )
        await self._conn.commit()

    async def set_arena_judgment_judging(self, judgment_id: str, attempt_token: str) -> None:
        """Mark an Arena judgment as JUDGING, if this attempt still owns it.

        Args:
            judgment_id: UUID of the Arena judgment.
            attempt_token: Claim stamped by this attempt's dispatch.

        Raises:
            JudgmentOwnershipLost: If another attempt has claimed the judgment.
        """
        result = await self._conn.execute(
            update(_arena_submission_judgment)
            .where(self._claim_predicate(AttemptClaim(_arena_submission_judgment, judgment_id, attempt_token)))
            .values(status=JudgmentStatus.JUDGING.value)
        )
        if result.rowcount == 0:
            await self._conn.rollback()
            raise JudgmentOwnershipLost(f"Arena judgment {judgment_id} was claimed by another attempt")
        await self._conn.commit()

    async def set_arena_judgment_done(
        self,
        submission: QueuedArenaSubmission,
        verdict: Verdict,
        *,
        attempt_token: str,
        compile_log: str | None = None,
        max_wall_time_ms: int | None = None,
        max_memory_kb: int | None = None,
        max_output_bytes: int | None = None,
    ) -> None:
        """Persist the final Arena verdict and reconcile the pair's solver row.

        Fenced on this attempt's claim, so a verdict is only ever written by the
        attempt that owns the judgment. Without the fence a stalled attempt
        finishing late would overwrite its replacement's verdict, and its
        notification and first-solve accounting would run a second time.

        The solver reconciliation runs on every finishing judgment, not only on
        an Accepted verdict: a rejudge can withdraw an AC, and a rejudge to AC again leaves
        a ``solved_at`` copied from a superseded judgment. See
        :meth:`~autojudge.db._arena_solver._ArenaSolverMixin.reconcile_arena_solver`.

        Args:
            submission: The judged Arena submission payload.
            verdict: Final verdict to persist.
            attempt_token: Claim stamped by this attempt's dispatch.
            compile_log: Compiler output to persist (may be None).
            max_wall_time_ms: Worst-case wall time across test cases.
            max_memory_kb: Peak memory across test cases.
            max_output_bytes: Peak stdout size across test cases.

        Raises:
            JudgmentOwnershipLost: If another attempt has claimed the judgment.
        """
        now = _utcnow()
        result = await self._conn.execute(
            update(_arena_submission_judgment)
            .where(
                self._claim_predicate(AttemptClaim(_arena_submission_judgment, submission.judgment_id, attempt_token))
            )
            .values(
                status=JudgmentStatus.DONE.value,
                autojudge_verdict=verdict.value,
                final_verdict=verdict.value,
                compile_log=compile_log,
                max_wall_time_ms=max_wall_time_ms,
                max_memory_kb=max_memory_kb,
                max_output_bytes=max_output_bytes,
                finished_at=now,
            )
        )
        if result.rowcount == 0:
            await self._conn.rollback()
            raise JudgmentOwnershipLost(f"Arena judgment {submission.judgment_id} was claimed by another attempt")
        await self.reconcile_arena_solver(
            submission.user_id, submission.problem_id, verdict_is_ac=verdict == Verdict.AC
        )
        await create_arena_notification(
            self._conn,
            user_id=submission.user_id,
            notification_kind=ArenaNotificationKind.SUBMISSION_JUDGED,
            title="Submission judged",
            message=(
                f"Your submission for problem {submission.problem_number} - "
                f"{submission.problem_title} was judged. View the result."
            ),
            target_url=f"/submissions/{submission.submission_id}",
            source_ref=submission.judgment_id,
            context={
                "submission_id": submission.submission_id,
                "judgment_id": submission.judgment_id,
                "problem_id": submission.problem_id,
                "problem_number": submission.problem_number,
                "verdict": verdict.value,
            },
        )
        await self._conn.commit()

    async def set_arena_judgment_failed(
        self,
        judgment_id: str,
        error_message: str,
        attempt_token: str | None = None,
    ) -> None:
        """Mark an Arena judgment as FAILED due to an internal judge error.

        Fenced twice. On a non-terminal status, so a losing attempt cannot stamp
        ``FAILED`` over a verdict already committed; and, when the caller knows
        its ``attempt_token``, on a claim that is either this attempt's or absent
        — a judgment claimed by *another* attempt is that attempt's to finish,
        while an unclaimed one (the job failed before dispatch could stamp it)
        is legitimately ours to fail. A fenced-out call is a logged no-op.

        A judgment that reaches ``FAILED`` is terminal and produced no verdict,
        so the submitter's solver row is reconciled here too. Without it, an
        Accepted judgment superseded for a rejudge that then failed would leave
        the pair counted as solved with no Accepted submission behind it.

        Args:
            judgment_id: UUID of the Arena judgment.
            error_message: Internal error description for admins.
            attempt_token: Claim stamped by this attempt's dispatch, when it got
                as far as dispatching.
        """
        # A prior statement (e.g. a poison-pill INSERT) may have left the
        # transaction aborted; roll back so this UPDATE runs in a clean one and
        # the judgment can actually reach a terminal state instead of looping.
        await self._conn.rollback()
        claim_filter = (
            or_(
                _arena_submission_judgment.c.attempt_token.is_(None),
                _arena_submission_judgment.c.attempt_token == attempt_token,
            )
            if attempt_token is not None
            else true()
        )
        result = await self._conn.execute(
            update(_arena_submission_judgment)
            .where(
                _arena_submission_judgment.c.id == judgment_id,
                _arena_submission_judgment.c.status.in_(_DISPATCHABLE_STATUSES),
                claim_filter,
            )
            .values(
                status=JudgmentStatus.FAILED.value,
                error_message=error_message,
                finished_at=_utcnow(),
            )
        )
        if result.rowcount == 0:
            logger.warning(
                "Arena judgment %s already terminal; not overwriting it with FAILED: %s",
                judgment_id,
                error_message,
            )
        else:
            await self.reconcile_arena_solvers_for_judgments([judgment_id])
        await self._conn.commit()

    async def insert_arena_test_result(
        self,
        *,
        judgment_id: str,
        test_case_id: str,
        verdict: Verdict,
        wall_time_ms: int | None,
        memory_kb: int | None,
        exit_code: int | None,
        exit_signal: int | None,
        stdout_excerpt: bytes,
        stderr_excerpt: bytes,
        attempt_token: str | None = None,
    ) -> None:
        """Persist the first non-AC Arena test result for the owning attempt.

        Written only while this attempt still holds the judgment's claim, and
        tolerant of the attempt replaying its own write. See
        ``_insert_result_row_once``.

        Raises:
            JudgmentOwnershipLost: If another attempt has claimed the judgment.
            RuntimeError: If a committed row blames a different case or verdict.
        """
        await self._insert_result_row_once(
            _arena_submission_test_result,
            values={
                "id": str(uuid.uuid4()),
                "judgment_id": judgment_id,
                "test_case_id": test_case_id,
                "verdict": verdict.value,
                "wall_time_ms": wall_time_ms,
                "memory_kb": memory_kb,
                "exit_code": exit_code,
                "exit_signal": exit_signal,
                "stdout_excerpt": decode_for_text_column(stdout_excerpt),
                "stderr_excerpt": decode_for_text_column(stderr_excerpt),
                "created_at": _utcnow(),
            },
            index_elements=("judgment_id",),
            # The row is keyed by judgment alone, so which case failed and how
            # are both part of what the two attempts must agree on.
            identity_columns=("test_case_id", "verdict"),
            volatile_columns=RESULT_VOLATILE_COLUMNS,
            claim=self._arena_claim(judgment_id, attempt_token),
        )
        await self._conn.commit()

    @staticmethod
    def _arena_claim(judgment_id: str, attempt_token: str | None) -> AttemptClaim | None:
        """Build this attempt's claim on an Arena judgment, if it holds one."""
        if attempt_token is None:
            return None
        return AttemptClaim(_arena_submission_judgment, judgment_id, attempt_token)

    async def _get_arena_test_cases(
        self,
        problem_id: str,
        *,
        require_expected_output: bool = True,
    ) -> list[ArenaQueuedTestCase]:
        """Load Arena test cases: ordinals from the DB, content from the filesystem.

        Content lives under ``<root>/arena/<problem_id>/NNN.in|out`` (the Arena
        identity domain). The DB row supplies only the stable ``test_case_id`` and
        ``ordinal``; bytes are read and LF-normalized from disk.

        Args:
            problem_id: Arena problem whose cases to load.
            require_expected_output: False for validator problems, whose cases hold
                input only; their ``expected_output`` comes back empty.

        Raises:
            FileNotFoundError: If a referenced ``.in`` file (or, when required, its
                ``.out`` file) is missing.
        """
        from autojudge.config import settings

        rows = await self._conn.execute(
            select(
                _arena_test_case.c.id,
                _arena_test_case.c.ordinal,
            )
            .where(_arena_test_case.c.problem_id == problem_id)
            .order_by(_arena_test_case.c.ordinal)
        )
        testcase_dir = settings.arena_testcase_dir
        cases: list[ArenaQueuedTestCase] = []
        for row in rows.fetchall():
            ordinal = cast(int, row.ordinal)
            in_path = get_testcase_path(problem_id, ordinal, "in", testcase_dir)
            out_path = get_testcase_path(problem_id, ordinal, "out", testcase_dir)
            if not in_path.exists() or (require_expected_output and not out_path.exists()):
                raise FileNotFoundError(
                    f"Missing Arena test case file for problem '{problem_id}' ordinal {ordinal:03d}."
                )
            expected_output = b""
            if require_expected_output:
                expected_output = normalize_testcase_bytes(out_path.read_bytes()[:_ARENA_TEST_FILE_MAX_BYTES])
            cases.append(
                ArenaQueuedTestCase(
                    test_case_id=cast(str, row.id),
                    ordinal=ordinal,
                    input_data=normalize_testcase_bytes(in_path.read_bytes()[:_ARENA_TEST_FILE_MAX_BYTES]),
                    expected_output=expected_output,
                )
            )
        return cases
