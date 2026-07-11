#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Database operations for Arena submission judging."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import cast

from sqlalchemy import delete, func, select, update

from autojudge.db._base import _DatabaseBase, _utcnow
from autojudge.runtime_utils import decode_for_text_column
from autojudge.types import (
    ArenaQueuedTestCase,
    ProblemLimits,
    QueuedArenaSubmission,
    RecoverableArenaSubmissionJob,
)
from shared.db_schema.arena import arena_problem_ratings as _arena_problem_rating
from shared.db_schema.arena import arena_problem_solvers as _arena_problem_solver
from shared.db_schema.arena import arena_problems as _arena_problem
from shared.db_schema.arena import arena_submission_judgments as _arena_submission_judgment
from shared.db_schema.arena import arena_submission_test_results as _arena_submission_test_result
from shared.db_schema.arena import arena_submissions as _arena_submission
from shared.db_schema.arena import arena_test_cases as _arena_test_case
from shared.enumerations import ArenaNotificationKind, JudgmentStatus, Verdict
from shared.services.arena_notification_service import create_arena_notification
from shared.services.arena_query_helpers import is_excluded_from_problem_rating
from shared.services.testcase_files import get_testcase_path
from shared.tc_zip import normalize_testcase_bytes

# Maximum bytes read from a single Arena test case file (input or expected output).
_ARENA_TEST_FILE_MAX_BYTES = 256 * 1024 * 1024  # 256 MB


class _ArenaSubmissionMixin(_DatabaseBase):
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

        test_cases = await self._get_arena_test_cases(cast(str, result["problem_id"]))
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
            .where(
                _arena_submission_judgment.c.status.in_(
                    (JudgmentStatus.QUEUED.value, JudgmentStatus.DISPATCHED.value, JudgmentStatus.JUDGING.value)
                )
            )
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

    async def set_arena_judgment_dispatched(self, judgment_id: str, worker_id: str) -> None:
        """Mark an Arena judgment as DISPATCHED and clear stale result state."""
        now = _utcnow()
        await self._conn.execute(
            delete(_arena_submission_test_result).where(
                _arena_submission_test_result.c.judgment_id == judgment_id,
            )
        )
        await self._conn.execute(
            update(_arena_submission_judgment)
            .where(_arena_submission_judgment.c.id == judgment_id)
            .values(
                status=JudgmentStatus.DISPATCHED.value,
                worker_id=worker_id,
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
        await self._conn.commit()

    async def set_arena_judgment_judging(self, judgment_id: str) -> None:
        """Mark an Arena judgment as JUDGING."""
        await self._conn.execute(
            update(_arena_submission_judgment)
            .where(_arena_submission_judgment.c.id == judgment_id)
            .values(status=JudgmentStatus.JUDGING.value)
        )
        await self._conn.commit()

    async def set_arena_judgment_done(
        self,
        submission: QueuedArenaSubmission,
        verdict: Verdict,
        *,
        compile_log: str | None = None,
        max_wall_time_ms: int | None = None,
        max_memory_kb: int | None = None,
        max_output_bytes: int | None = None,
    ) -> None:
        """Persist the final Arena verdict and update first-solve stats."""
        now = _utcnow()
        await self._conn.execute(
            update(_arena_submission_judgment)
            .where(_arena_submission_judgment.c.id == submission.judgment_id)
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
        if verdict == Verdict.AC:
            await self._record_first_arena_solve(submission, now)
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

    async def set_arena_judgment_failed(self, judgment_id: str, error_message: str) -> None:
        """Mark an Arena judgment as FAILED due to an internal judge error."""
        # A prior statement (e.g. a poison-pill INSERT) may have left the
        # transaction aborted; roll back so this UPDATE runs in a clean one and
        # the judgment can actually reach a terminal state instead of looping.
        await self._conn.rollback()
        await self._conn.execute(
            update(_arena_submission_judgment)
            .where(_arena_submission_judgment.c.id == judgment_id)
            .values(
                status=JudgmentStatus.FAILED.value,
                error_message=error_message,
                finished_at=_utcnow(),
            )
        )
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
    ) -> None:
        """Persist the first non-AC Arena test result for a judgment."""
        await self._conn.execute(
            _arena_submission_test_result.insert().values(
                id=str(uuid.uuid4()),
                judgment_id=judgment_id,
                test_case_id=test_case_id,
                verdict=verdict.value,
                wall_time_ms=wall_time_ms,
                memory_kb=memory_kb,
                exit_code=exit_code,
                exit_signal=exit_signal,
                stdout_excerpt=decode_for_text_column(stdout_excerpt),
                stderr_excerpt=decode_for_text_column(stderr_excerpt),
                created_at=_utcnow(),
            )
        )
        await self._conn.commit()

    async def _get_arena_test_cases(self, problem_id: str) -> list[ArenaQueuedTestCase]:
        """Load Arena test cases: ordinals from the DB, content from the filesystem.

        Content lives under ``<root>/arena/<problem_id>/NNN.in|out`` (the Arena
        identity domain). The DB row supplies only the stable ``test_case_id`` and
        ``ordinal``; bytes are read and LF-normalized from disk.

        Raises:
            FileNotFoundError: If a referenced ``.in``/``.out`` file is missing.
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
            if not in_path.exists() or not out_path.exists():
                raise FileNotFoundError(
                    f"Missing Arena test case file for problem '{problem_id}' ordinal {ordinal:03d}."
                )
            cases.append(
                ArenaQueuedTestCase(
                    test_case_id=cast(str, row.id),
                    ordinal=ordinal,
                    input_data=normalize_testcase_bytes(in_path.read_bytes()[:_ARENA_TEST_FILE_MAX_BYTES]),
                    expected_output=normalize_testcase_bytes(out_path.read_bytes()[:_ARENA_TEST_FILE_MAX_BYTES]),
                )
            )
        return cases

    async def _record_first_arena_solve(self, submission: QueuedArenaSubmission, solved_at: datetime) -> None:
        """Record personal first solve and participant-only aggregate solve stats."""
        existing = await self._conn.execute(
            select(_arena_problem_solver.c.problem_id).where(
                _arena_problem_solver.c.problem_id == submission.problem_id,
                _arena_problem_solver.c.user_id == submission.user_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return

        tries = await self._conn.scalar(
            select(func.count())
            .select_from(_arena_submission)
            .where(
                _arena_submission.c.problem_id == submission.problem_id,
                _arena_submission.c.user_id == submission.user_id,
                _arena_submission.c.created_at
                <= select(_arena_submission.c.created_at)
                .where(_arena_submission.c.id == submission.submission_id)
                .scalar_subquery(),
            )
        )
        await self._conn.execute(
            _arena_problem_solver.insert().values(
                problem_id=submission.problem_id,
                user_id=submission.user_id,
                solved_at=solved_at,
            )
        )
        owner_id = await self._conn.scalar(
            select(_arena_problem.c.owner_id).where(_arena_problem.c.id == submission.problem_id)
        )
        # The problem owner never inflates the aggregate solve stats.
        if is_excluded_from_problem_rating(submission.user_id, owner_id):
            return
        await self._conn.execute(
            update(_arena_problem_rating)
            .where(_arena_problem_rating.c.problem_id == submission.problem_id)
            .values(
                solved_users=_arena_problem_rating.c.solved_users + 1,
                total_tries_before_solve=_arena_problem_rating.c.total_tries_before_solve + int(tries or 0),
            )
        )
