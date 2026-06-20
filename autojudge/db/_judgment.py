#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
autojudge/db/_judgment.py

Mixin for judgment state machine transitions and balloon task creation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from autojudge.db._base import _DatabaseBase, _utcnow
from autojudge.types import QueuedSubmission
from shared.db_schema import submission_judgments as _submission_judgment
from shared.db_schema import submission_test_results as _submission_test_result
from shared.db_schema import submissions as _submission
from shared.db_schema import tasks as _task
from shared.enumerations import JudgmentStatus, TaskType, Verdict
from shared.timing import compute_timestamp_seconds


class _JudgmentMixin(_DatabaseBase):
    """Judgment status transitions and related task creation."""

    async def set_judgment_dispatched(
        self,
        judgment_id: str,
        worker_id: str,
        contest_start_time: datetime | None = None,
    ) -> None:
        """
        Mark judgment as picked up by this worker (DISPATCHED).

        Retry-safe: clears partial per-test-case rows and stale runtime
        metadata so the same judgment can be processed again from scratch.

        Args:
            judgment_id: UUID of the judgment.
            worker_id: Stable worker identity string.
            contest_start_time: Used for audit timestamp_seconds computation.
        """
        now = _utcnow()
        state = await self._get_judgment_state(judgment_id)
        await self._conn.execute(
            delete(_submission_test_result).where(_submission_test_result.c.judgment_id == judgment_id)
        )
        await self._conn.execute(
            _submission_judgment.update()
            .where(_submission_judgment.c.id == judgment_id)
            .values(
                status=JudgmentStatus.DISPATCHED,
                worker_id=worker_id,
                started_at=now,
                finished_at=None,
                autojudge_verdict=None,
                final_verdict=None,
                compile_log=None,
                max_wall_time_ms=None,
                max_memory_kb=None,
                min_wall_time_ms=None,
                min_memory_kb=None,
                error_message=None,
            )
        )
        await self._insert_judgment_audit(
            judgment_id=judgment_id,
            submission_id=state["submission_id"],
            event_type="STATUS_CHANGE",
            from_status=state["status"],
            to_status=JudgmentStatus.DISPATCHED,
            from_verdict=state["autojudge_verdict"],
            to_verdict=state["autojudge_verdict"],
            message=f"Worker {worker_id} dispatched judgment",
            contest_start_time=contest_start_time,
        )
        await self._conn.commit()

    async def set_judgment_judging(
        self,
        judgment_id: str,
        contest_start_time: datetime | None = None,
    ) -> None:
        """
        Mark judgment as actively running test cases (JUDGING).

        Args:
            judgment_id: UUID of the judgment.
            contest_start_time: Used for audit timestamp_seconds computation.
        """
        state = await self._get_judgment_state(judgment_id)
        await self._conn.execute(
            _submission_judgment.update()
            .where(_submission_judgment.c.id == judgment_id)
            .values(status=JudgmentStatus.JUDGING)
        )
        await self._insert_judgment_audit(
            judgment_id=judgment_id,
            submission_id=state["submission_id"],
            event_type="STATUS_CHANGE",
            from_status=state["status"],
            to_status=JudgmentStatus.JUDGING,
            from_verdict=state["autojudge_verdict"],
            to_verdict=state["autojudge_verdict"],
            message="Worker started test-case execution",
            contest_start_time=contest_start_time,
        )
        await self._conn.commit()

    async def set_judgment_done(
        self,
        judgment_id: str,
        verdict: Verdict,
        *,
        autojudge_only: bool,
        contest_start_time: datetime | None = None,
        compile_log: str | None = None,
        max_wall_time_ms: int | None = None,
        max_memory_kb: int | None = None,
        min_wall_time_ms: int | None = None,
        min_memory_kb: int | None = None,
    ) -> None:
        """
        Write final verdict and mark judgment as DONE.

        Args:
            judgment_id: UUID of the judgment.
            verdict: The autojudge verdict to persist.
            autojudge_only: Whether to also set final_verdict immediately.
            contest_start_time: Used for audit timestamp_seconds computation.
            compile_log: Compiler output to persist (may be None).
            max_wall_time_ms: Worst-case wall time across test cases.
            max_memory_kb: Peak memory across test cases.
            min_wall_time_ms: Best-case wall time across test cases.
            min_memory_kb: Minimum memory across test cases.
        """
        now = _utcnow()
        state = await self._get_judgment_state(judgment_id)
        await self._conn.execute(
            _submission_judgment.update()
            .where(_submission_judgment.c.id == judgment_id)
            .values(
                status=JudgmentStatus.DONE,
                autojudge_verdict=verdict,
                final_verdict=verdict if autojudge_only else None,
                compile_log=compile_log,
                max_wall_time_ms=max_wall_time_ms,
                max_memory_kb=max_memory_kb,
                min_wall_time_ms=min_wall_time_ms,
                min_memory_kb=min_memory_kb,
                finished_at=now,
            )
        )
        await self._insert_judgment_audit(
            judgment_id=judgment_id,
            submission_id=state["submission_id"],
            event_type="STATUS_CHANGE",
            from_status=state["status"],
            to_status=JudgmentStatus.DONE,
            from_verdict=state["autojudge_verdict"],
            to_verdict=verdict,
            message="Worker finalized judgment",
            contest_start_time=contest_start_time,
        )
        await self._conn.commit()

    async def set_judgment_failed(
        self,
        judgment_id: str,
        error_message: str,
        contest_start_time: datetime | None = None,
    ) -> None:
        """
        Mark judgment as FAILED due to an internal judge error.

        Not a CE — the contestant is not at fault. The submission can be
        rejudged once the underlying issue is resolved.

        Args:
            judgment_id: UUID of the judgment.
            error_message: Internal error description for admins.
            contest_start_time: Used for audit timestamp_seconds computation.
        """
        now = _utcnow()
        state = await self._get_judgment_state(judgment_id)
        await self._conn.execute(
            _submission_judgment.update()
            .where(_submission_judgment.c.id == judgment_id)
            .values(status=JudgmentStatus.FAILED, error_message=error_message, finished_at=now)
        )
        await self._insert_judgment_audit(
            judgment_id=judgment_id,
            submission_id=state["submission_id"],
            event_type="STATUS_CHANGE",
            from_status=state["status"],
            to_status=JudgmentStatus.FAILED,
            from_verdict=state["autojudge_verdict"],
            to_verdict=state["autojudge_verdict"],
            message=error_message,
            contest_start_time=contest_start_time,
        )
        await self._conn.commit()

    async def create_balloon_task_if_needed(
        self,
        submission: QueuedSubmission,
        verdict: Verdict,
    ) -> bool:
        """
        Create a balloon task when an autojudge-only contest yields an AC verdict.

        Idempotent: skips creation if a balloon-like task already exists for the
        same (team_id, problem_id) pair, or if the scoreboard is currently frozen.

        Args:
            submission: The queued submission that was just judged.
            verdict: The final verdict produced by the autojudge.

        Returns:
            True if a new task was created, False otherwise.
        """
        is_accepted = verdict == Verdict.AC or (verdict == Verdict.PE and submission.accept_pe)
        if not is_accepted:
            return False

        now = _utcnow()
        scoreboard_freeze_at = submission.contest_start_time + timedelta(minutes=submission.stop_updating_scoreboard)
        comparable_now = now.replace(tzinfo=None) if scoreboard_freeze_at.tzinfo is None else now
        scoreboard_frozen = comparable_now > scoreboard_freeze_at
        if scoreboard_frozen:
            return False

        existing = await self._conn.execute(
            select(_task.c.id).where(
                _task.c.team_id == submission.team_id,
                _task.c.problem_id == submission.problem_id,
                _task.c.type.in_((TaskType.BALLOON, TaskType.FIRST_BALLOON)),
            )
        )
        if existing.scalar_one_or_none() is not None:
            return False

        accepted_verdicts = (Verdict.AC, Verdict.PE) if submission.accept_pe else (Verdict.AC,)
        first_accepted = await self._conn.execute(
            select(_submission.c.id)
            .join(_submission_judgment, _submission_judgment.c.submission_id == _submission.c.id)
            .where(
                _submission.c.problem_id == submission.problem_id,
                _submission_judgment.c.status != JudgmentStatus.SUPERSEDED,
                _submission_judgment.c.final_verdict.in_(accepted_verdicts),
            )
            .order_by(_submission.c.timestamp_seconds, _submission.c.created_at, _submission.c.id)
            .limit(1)
        )
        task_type = (
            TaskType.FIRST_BALLOON
            if first_accepted.scalar_one_or_none() == submission.submission_id
            else TaskType.BALLOON
        )
        timestamp_seconds = compute_timestamp_seconds(submission.contest_start_time, now)
        values = {
            "id": str(uuid.uuid4()),
            "team_id": submission.team_id,
            "problem_id": submission.problem_id,
            "type": task_type,
            "source_code": "",
            "source_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "source_size_bytes": 0,
            "created_at": now,
            "updated_at": now,
            "created_timestamp_seconds": timestamp_seconds,
        }
        if task_type == TaskType.FIRST_BALLOON:
            nested = await self._conn.begin_nested()
            try:
                await self._conn.execute(_task.insert().values(**values))
                await nested.commit()
                await self._conn.commit()
                return True
            except IntegrityError:
                await nested.rollback()
                values["id"] = str(uuid.uuid4())
                values["type"] = TaskType.BALLOON

        await self._conn.execute(_task.insert().values(**values))
        await self._conn.commit()
        return True
