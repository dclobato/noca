#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
autojudge/db/_submission.py

Mixin for loading submission data from the database.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import select

from autojudge.db._base import _DatabaseBase
from autojudge.types import QueuedSubmission, RecoverableSubmissionJob
from shared.db_schema import contests as _contest
from shared.db_schema import problems as _problem
from shared.db_schema import submission_judgments as _submission_judgment
from shared.db_schema import submissions as _submission
from shared.enumerations import JudgmentStatus


class _SubmissionMixin(_DatabaseBase):
    """Database operations for loading submission payloads."""

    async def get_submission_for_judging(self, judgment_id: str) -> QueuedSubmission:
        """
        Load the full submission payload for a pending judgment.

        Args:
            judgment_id: UUID of the judgment to load.

        Returns:
            QueuedSubmission ready for the worker pipeline.

        Raises:
            LookupError: If the judgment is not found or is already terminal.
        """
        row = await self._conn.execute(
            select(
                _submission_judgment.c.id.label("judgment_id"),
                _submission_judgment.c.status,
                _submission.c.id.label("submission_id"),
                _problem.c.contest_id,
                _contest.c.start_time,
                _submission.c.problem_id,
                _submission.c.team_id,
                _submission.c.language_id,
                _submission.c.source_code,
                _contest.c.autojudge_only,
                _contest.c.accept_pe,
                _contest.c.stop_updating_scoreboard,
            )
            .select_from(
                _submission_judgment.join(_submission, _submission.c.id == _submission_judgment.c.submission_id)
                .join(_problem, _problem.c.id == _submission.c.problem_id)
                .join(_contest, _contest.c.id == _problem.c.contest_id)
            )
            .where(_submission_judgment.c.id == judgment_id)
        )
        result = row.mappings().first()
        if result is None:
            raise LookupError(f"Judgment '{judgment_id}' not found in database")

        status = cast(JudgmentStatus, result["status"])
        if status in {JudgmentStatus.DONE, JudgmentStatus.FAILED, JudgmentStatus.SUPERSEDED}:
            raise LookupError(f"Judgment '{judgment_id}' is not judgeable (status={status})")

        return QueuedSubmission(
            judgment_id=cast(str, result["judgment_id"]),
            submission_id=cast(str, result["submission_id"]),
            contest_id=cast(str, result["contest_id"]),
            contest_start_time=cast(datetime, result["start_time"]),
            problem_id=cast(str, result["problem_id"]),
            team_id=cast(str, result["team_id"]),
            language_id=cast(str, result["language_id"]),
            source_code=cast(str, result["source_code"]),
            autojudge_only=cast(bool, result["autojudge_only"]),
            accept_pe=cast(bool, result["accept_pe"]),
            stop_updating_scoreboard=cast(int, result["stop_updating_scoreboard"]),
        )

    async def list_recoverable_submission_jobs(self) -> list[RecoverableSubmissionJob]:
        """
        Return all non-terminal submission judgments that can be re-enqueued.

        Returns:
            List of RecoverableSubmissionJob ordered by created_at.
        """
        rows = await self._conn.execute(
            select(
                _submission_judgment.c.id.label("judgment_id"),
                _submission_judgment.c.status,
                _submission.c.id.label("submission_id"),
                _problem.c.contest_id,
                _contest.c.start_time,
                _submission.c.problem_id,
                _submission.c.team_id,
                _submission.c.language_id,
                _submission.c.source_code,
                _contest.c.autojudge_only,
                _contest.c.accept_pe,
                _contest.c.stop_updating_scoreboard,
            )
            .select_from(
                _submission_judgment.join(_submission, _submission.c.id == _submission_judgment.c.submission_id)
                .join(_problem, _problem.c.id == _submission.c.problem_id)
                .join(_contest, _contest.c.id == _problem.c.contest_id)
            )
            .where(
                _submission_judgment.c.status.in_(
                    (JudgmentStatus.QUEUED, JudgmentStatus.DISPATCHED, JudgmentStatus.JUDGING)
                )
            )
            .order_by(_submission_judgment.c.created_at)
        )
        result: list[RecoverableSubmissionJob] = []
        for row in rows.mappings().all():
            result.append(
                RecoverableSubmissionJob(
                    status=cast(JudgmentStatus, row["status"]),
                    payload=QueuedSubmission(
                        judgment_id=cast(str, row["judgment_id"]),
                        submission_id=cast(str, row["submission_id"]),
                        contest_id=cast(str, row["contest_id"]),
                        contest_start_time=cast(datetime, row["start_time"]),
                        problem_id=cast(str, row["problem_id"]),
                        team_id=cast(str, row["team_id"]),
                        language_id=cast(str, row["language_id"]),
                        source_code=cast(str, row["source_code"]),
                        autojudge_only=cast(bool, row["autojudge_only"]),
                        accept_pe=cast(bool, row["accept_pe"]),
                        stop_updating_scoreboard=cast(int, row["stop_updating_scoreboard"]),
                    ),
                )
            )
        return result
