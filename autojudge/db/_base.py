#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
autojudge/db/_base.py

Base class for worker DB access: connection holder and shared private helpers
used by all mixin classes (_get_judgment_state, _insert_judgment_audit).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import cast

from sqlalchemy.ext.asyncio import AsyncConnection

from autojudge.types import _JudgmentState
from shared.db_schema import submission_judgment_audit as _submission_judgment_audit
from shared.db_schema import submission_judgments as _submission_judgment
from shared.enumerations import JudgmentStatus, Verdict
from shared.timing import compute_timestamp_seconds


def _utcnow() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


class _DatabaseBase:
    """Holds the async connection and shared private helpers for all DB mixins."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def _get_judgment_state(self, judgment_id: str) -> _JudgmentState:
        """
        Fetch a lightweight snapshot of a judgment's current state.

        Args:
            judgment_id: UUID string of the judgment.

        Returns:
            _JudgmentState with submission_id, status, and autojudge_verdict.

        Raises:
            LookupError: If the judgment does not exist.
        """
        row = await self._conn.execute(_submission_judgment.select().where(_submission_judgment.c.id == judgment_id))
        result = row.fetchone()
        if result is None:
            raise LookupError(f"Judgment '{judgment_id}' not found in database")
        return {
            "submission_id": cast(str, result.submission_id),
            "status": cast(JudgmentStatus | None, result.status),
            "autojudge_verdict": cast(Verdict | None, result.autojudge_verdict),
        }

    async def _insert_judgment_audit(
        self,
        judgment_id: str,
        submission_id: str,
        event_type: str,
        from_status: JudgmentStatus | None,
        to_status: JudgmentStatus | None,
        from_verdict: Verdict | None,
        to_verdict: Verdict | None,
        message: str | None = None,
        contest_start_time: datetime | None = None,
    ) -> None:
        """
        Insert one judgment audit row (does not commit — caller commits).

        Args:
            judgment_id: Judgment being audited.
            submission_id: Parent submission.
            event_type: Audit event category string.
            from_status: Previous judgment status.
            to_status: New judgment status.
            from_verdict: Previous autojudge verdict.
            to_verdict: New autojudge verdict.
            message: Optional human-readable note.
            contest_start_time: Used to compute timestamp_seconds; may be None.
        """
        now = _utcnow()
        await self._conn.execute(
            _submission_judgment_audit.insert().values(
                id=str(uuid.uuid4()),
                judgment_id=judgment_id,
                submission_id=submission_id,
                actor_user_id=None,
                event_source="WORKER",
                event_type=event_type,
                from_status=from_status,
                to_status=to_status,
                from_verdict=from_verdict,
                to_verdict=to_verdict,
                message=message,
                created_at=now,
                timestamp_seconds=compute_timestamp_seconds(contest_start_time, now) if contest_start_time else None,
            )
        )
