#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Service for teacher feedback on Arena submissions.

Teacher feedback is a sparse 1:1 record keyed by ``submission_id``. A teacher
(or admin) writes feedback on a student's non-AC, set-tied submission; editing
replaces the row and refreshes ``feedback_at``. All helpers leave transaction
ownership to the caller and never commit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena import arena_submission_teacher_feedback


def _dialect_name(executor: Any) -> str:
    """Return the SQLAlchemy dialect name backing the given session/connection."""
    dialect = getattr(executor, "dialect", None)
    if dialect is not None:
        return cast(str, dialect.name)
    bind = executor.get_bind()
    return cast(str, bind.dialect.name)


async def upsert_teacher_feedback(
    session: AsyncSession,
    *,
    submission_id: str,
    teacher_id: str,
    feedback_text: str,
    feedback_at: datetime | None = None,
) -> datetime:
    """Insert or replace the teacher feedback for one submission.

    Uses ``INSERT ... ON CONFLICT (submission_id) DO UPDATE`` so editing existing
    feedback overwrites the text and refreshes ``feedback_at`` and ``teacher_id``.
    Does not commit; the caller owns the transaction.

    Args:
        session: Active async database session.
        submission_id: UUID of the ``arena_submissions`` row.
        teacher_id: UUID of the authoring teacher/admin.
        feedback_text: The feedback body.
        feedback_at: Timestamp to record; defaults to the current UTC time.

    Returns:
        datetime: The ``feedback_at`` value that was written (useful for building
        a per-update notification ``source_ref``).
    """
    written_at = feedback_at or datetime.now(UTC)
    values = {
        "submission_id": submission_id,
        "teacher_id": teacher_id,
        "feedback_text": feedback_text,
        "feedback_at": written_at,
    }
    update_set = {
        "teacher_id": teacher_id,
        "feedback_text": feedback_text,
        "feedback_at": written_at,
    }

    stmt: Any
    if _dialect_name(session) == "postgresql":
        stmt = (
            postgresql_insert(arena_submission_teacher_feedback)
            .values(**values)
            .on_conflict_do_update(index_elements=["submission_id"], set_=update_set)
        )
    else:
        stmt = (
            sqlite_insert(arena_submission_teacher_feedback)
            .values(**values)
            .on_conflict_do_update(index_elements=["submission_id"], set_=update_set)
        )

    await session.execute(stmt)
    return written_at


async def delete_teacher_feedback(session: AsyncSession, *, submission_id: str) -> bool:
    """Delete the teacher feedback for one submission, if any.

    Does not commit; the caller owns the transaction.

    Args:
        session: Active async database session.
        submission_id: UUID of the ``arena_submissions`` row.

    Returns:
        bool: True when a feedback row existed and was deleted.
    """
    result = await session.execute(
        delete(arena_submission_teacher_feedback).where(
            arena_submission_teacher_feedback.c.submission_id == submission_id
        )
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def get_teacher_feedback_text(session: AsyncSession, submission_id: str) -> str | None:
    """Return the feedback text for a submission, or ``None`` when absent."""
    return await session.scalar(
        select(arena_submission_teacher_feedback.c.feedback_text).where(
            arena_submission_teacher_feedback.c.submission_id == submission_id
        )
    )
