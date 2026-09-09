#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Persistence helpers for overall teacher feedback on Arena problem sets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena import arena_problem_set_student_feedback, arena_submissions
from shared.problem_statement_markdown import validate_md_content


@dataclass(frozen=True, slots=True)
class ProblemSetStudentFeedback:
    """The current overall feedback written for a student's problem set."""

    problem_set_id: str
    student_id: str
    teacher_id: str | None
    feedback_text: str
    feedback_at: datetime


def _dialect_name(executor: Any) -> str:
    """Return the SQLAlchemy dialect name backing a session or connection."""
    dialect = getattr(executor, "dialect", None)
    if dialect is not None:
        return cast(str, dialect.name)
    return cast(str, executor.get_bind().dialect.name)


async def get_problem_set_student_feedback(
    session: AsyncSession, *, problem_set_id: str, student_id: str
) -> ProblemSetStudentFeedback | None:
    """Return the current feedback for a student and problem set, if it exists."""
    row = (
        await session.execute(
            select(
                arena_problem_set_student_feedback.c.problem_set_id,
                arena_problem_set_student_feedback.c.student_id,
                arena_problem_set_student_feedback.c.teacher_id,
                arena_problem_set_student_feedback.c.feedback_text,
                arena_problem_set_student_feedback.c.feedback_at,
            ).where(
                arena_problem_set_student_feedback.c.problem_set_id == problem_set_id,
                arena_problem_set_student_feedback.c.student_id == student_id,
            )
        )
    ).one_or_none()
    if row is None:
        return None
    return ProblemSetStudentFeedback(
        problem_set_id=row.problem_set_id,
        student_id=row.student_id,
        teacher_id=row.teacher_id,
        feedback_text=row.feedback_text,
        feedback_at=row.feedback_at,
    )


async def student_has_problem_set_submission(session: AsyncSession, *, problem_set_id: str, student_id: str) -> bool:
    """Return whether the student has at least one submission tied to the set."""
    return (
        await session.scalar(
            select(arena_submissions.c.id)
            .where(
                arena_submissions.c.problem_set_id == problem_set_id,
                arena_submissions.c.user_id == student_id,
            )
            .limit(1)
        )
    ) is not None


async def upsert_problem_set_student_feedback(
    session: AsyncSession,
    *,
    problem_set_id: str,
    student_id: str,
    teacher_id: str,
    feedback_text: str,
    feedback_at: datetime | None = None,
) -> datetime:
    """Validate and save the current Markdown feedback without committing.

    Links are intentionally permitted, matching teacher announcements. Raw HTML
    and images remain rejected by the shared Markdown validator.

    Raises:
        ValueError: When feedback is blank or contains unsupported Markdown.
    """
    clean_text = feedback_text.strip()
    errors = ["Feedback cannot be empty."] if not clean_text else validate_md_content(clean_text, allow_links=True)
    if errors:
        raise ValueError(" ".join(errors))

    written_at = feedback_at or datetime.now(UTC)
    values = {
        "problem_set_id": problem_set_id,
        "student_id": student_id,
        "teacher_id": teacher_id,
        "feedback_text": clean_text,
        "feedback_at": written_at,
    }
    update_set = {key: values[key] for key in ("teacher_id", "feedback_text", "feedback_at")}
    if _dialect_name(session) == "postgresql":
        statement: Any = (
            postgresql_insert(arena_problem_set_student_feedback)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["problem_set_id", "student_id"],
                set_=update_set,
            )
        )
    else:
        statement = (
            sqlite_insert(arena_problem_set_student_feedback)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["problem_set_id", "student_id"],
                set_=update_set,
            )
        )
    await session.execute(statement)
    return written_at


async def delete_problem_set_student_feedback(session: AsyncSession, *, problem_set_id: str, student_id: str) -> bool:
    """Delete feedback for one student and problem set, without committing."""
    result = await session.execute(
        delete(arena_problem_set_student_feedback).where(
            arena_problem_set_student_feedback.c.problem_set_id == problem_set_id,
            arena_problem_set_student_feedback.c.student_id == student_id,
        )
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]
