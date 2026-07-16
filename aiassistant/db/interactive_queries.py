#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""SQLAlchemy Core queries for interactive-problem AI review context.

Kept separate from the general AI-review queries so ``queries.py`` stays within
the project's source-size guidance. Like its sibling, this module operates only
on shared schema tables, preserving the module boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from shared.db_schema import (
    arena_problem_custom_validators,
    arena_sample_interactions,
    arena_submission_interactive_attempts,
    arena_submission_judgments,
    arena_submissions,
)
from shared.enumerations import CustomValidatorActiveState
from shared.services.sample_interactions import transcript_to_text


@dataclass
class InteractiveContext:
    """Interactive-problem context for an AI review, empty for batch problems.

    Attributes:
        verdict: Final verdict of the submission's latest judgment, or None when
            it has not been judged yet.
        failing_case_ordinal: 1-based secret test-case ordinal the deciding
            conversation belongs to, or None when no attempt was recorded.
        crash_reason: Judge-derived crash reason for the deciding attempt, if any.
        stderr_excerpt: Contestant stderr excerpt for the deciding attempt, if any.
        transcript_text: Deciding conversation serialized in the ``> ``/``< ``
            authoring format, or None when no attempt was recorded.
        transcript_truncated: Whether the judge truncated the recorded transcript.
        sample_transcripts: Author-provided public sample conversations, each an
            ``(transcript_text, explanation)`` pair, ordered by ordinal.
    """

    verdict: str | None
    failing_case_ordinal: int | None
    crash_reason: str | None
    stderr_excerpt: str | None
    transcript_text: str | None
    transcript_truncated: bool
    sample_transcripts: list[tuple[str, str | None]]


async def get_interactive_context(conn: AsyncConnection, submission_id: str) -> InteractiveContext | None:
    """Return interactive review context for a submission, or None if not interactive.

    A problem is interactive only when it has a ``VALID`` active custom validator.
    For such problems the deciding conversation lives in the retained attempts of
    the submission's latest judgment (the judge keeps only the last executed
    case's rows), and the public examples live in the author sample interactions.

    Args:
        conn: Active async database connection.
        submission_id: UUID string of the arena_submissions record.

    Returns:
        InteractiveContext when the problem has a VALID validator, else None.
    """
    problem_id = (
        await conn.execute(sa.select(arena_submissions.c.problem_id).where(arena_submissions.c.id == submission_id))
    ).scalar()
    if problem_id is None:
        return None

    active_state = (
        await conn.execute(
            sa.select(arena_problem_custom_validators.c.active_state).where(
                arena_problem_custom_validators.c.problem_id == problem_id
            )
        )
    ).scalar()
    if active_state != CustomValidatorActiveState.VALID:
        return None

    judgment = (
        (
            await conn.execute(
                sa.select(
                    arena_submission_judgments.c.id,
                    arena_submission_judgments.c.final_verdict,
                )
                .where(arena_submission_judgments.c.submission_id == submission_id)
                .order_by(arena_submission_judgments.c.created_at.desc())
                .limit(1)
            )
        )
        .mappings()
        .first()
    )

    verdict: str | None = None
    ordinal: int | None = None
    crash_reason: str | None = None
    stderr_excerpt: str | None = None
    transcript_text: str | None = None
    truncated = False
    if judgment is not None:
        verdict = str(judgment["final_verdict"]) if judgment["final_verdict"] else None
        attempt = (
            (
                await conn.execute(
                    sa.select(
                        arena_submission_interactive_attempts.c.test_case_ordinal,
                        arena_submission_interactive_attempts.c.crash_reason,
                        arena_submission_interactive_attempts.c.contestant_stderr_excerpt,
                        arena_submission_interactive_attempts.c.transcript,
                    )
                    .where(arena_submission_interactive_attempts.c.judgment_id == judgment["id"])
                    .order_by(arena_submission_interactive_attempts.c.attempt_number.desc())
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        if attempt is not None:
            ordinal = attempt["test_case_ordinal"]
            crash_reason = attempt["crash_reason"]
            stderr_excerpt = attempt["contestant_stderr_excerpt"]
            transcript = attempt["transcript"]
            if isinstance(transcript, dict):
                transcript_text = transcript_to_text(transcript) or None
                truncated = bool(transcript.get("truncated", False))

    sample_rows = (
        (
            await conn.execute(
                sa.select(
                    arena_sample_interactions.c.transcript,
                    arena_sample_interactions.c.explanation,
                )
                .where(
                    arena_sample_interactions.c.problem_id == problem_id,
                    arena_sample_interactions.c.hidden_at.is_(None),
                )
                .order_by(arena_sample_interactions.c.ordinal)
            )
        )
        .mappings()
        .all()
    )
    samples: list[tuple[str, str | None]] = []
    for row in sample_rows:
        if isinstance(row["transcript"], dict):
            text = transcript_to_text(row["transcript"])
            if text:
                samples.append((text, row["explanation"]))

    return InteractiveContext(
        verdict=verdict,
        failing_case_ordinal=ordinal,
        crash_reason=crash_reason,
        stderr_excerpt=stderr_excerpt,
        transcript_text=transcript_text,
        transcript_truncated=truncated,
        sample_transcripts=samples,
    )
