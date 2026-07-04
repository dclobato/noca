#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""SQLAlchemy Core queries for the AI Assistant worker.

All queries operate directly on shared schema tables — no arena ORM models are
imported. This preserves the architectural boundary between modules.

The ``EncryptedString`` TypeDecorator on ``arena_users._ai_api_key`` performs
transparent decryption when the column is read, provided ``init_encrypted_string``
was called at worker startup.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from shared.db_schema import (
    arena_ai_batch_jobs,
    arena_problems,
    arena_submission_ai_reviews,
    arena_submissions,
    arena_users,
)
from shared.enumerations import ARENA_AI_BATCH_JOB_TERMINAL_STATUSES, ArenaNotificationKind
from shared.services.arena_notification_service import create_arena_notification


@dataclass
class ProblemData:
    """Problem fields needed for AI review."""

    problem_statement: str | None
    image_base64: str | None
    image_mime: str | None
    image_caption: str | None


@dataclass
class SubmissionForReview:
    """Data fetched from arena_submissions needed to run an AI review."""

    submission_id: str
    source_code: str
    user_id: str
    problem_id: str
    problem_number: int
    problem_title: str
    language_id: str
    already_reviewed: bool


@dataclass
class StuckAIReviewSubmission:
    """A submission flagged for AI review that may have lost its queue job.

    Attributes:
        submission_id: UUID of the ``arena_submissions`` row.
        user_id: Owning Arena user UUID.
        problem_id: Problem UUID for the submission.
        language_id: Language key of the submission.
        use_platform_key: ``True`` when the owner has no personal API key, so the
            re-enqueued job should use the platform (batch) path.
    """

    submission_id: str
    user_id: str
    problem_id: str
    language_id: str
    use_platform_key: bool


async def get_stuck_ai_review_submissions(
    conn: AsyncConnection,
    older_than: datetime,
    limit: int,
) -> list[StuckAIReviewSubmission]:
    """Return submissions stuck pending AI review (no review, no active batch).

    A submission qualifies when ``submit_to_ai`` is set, it was last updated
    before ``older_than`` (a grace window that avoids racing a just-committed
    request whose enqueue is still in flight), it has no review row, and it has
    no *non-terminal* batch job (a non-terminal batch is genuinely in flight at
    OpenAI and the batch poller will finish it). A leftover *terminal* batch row
    does not disqualify: the worker guard deletes it and re-submits.

    The reconciler still cross-checks live queue presence in Valkey before
    re-enqueuing, so this query may return rows that are already queued.

    Args:
        conn: Active async database connection.
        older_than: Only submissions updated strictly before this instant.
        limit: Maximum number of rows to return in one sweep.

    Returns:
        List of ``StuckAIReviewSubmission`` ordered by ``updated_at`` ascending.
    """
    stmt = (
        sa.select(
            arena_submissions.c.id,
            arena_submissions.c.user_id,
            arena_submissions.c.problem_id,
            arena_submissions.c.language_id,
            arena_users.c["_ai_api_key"].is_(None).label("use_platform_key"),
        )
        .select_from(arena_submissions.join(arena_users, arena_submissions.c.user_id == arena_users.c.id))
        .where(
            arena_submissions.c.submit_to_ai.is_(True),
            arena_submissions.c.updated_at < older_than,
            ~sa.exists().where(arena_submission_ai_reviews.c.submission_id == arena_submissions.c.id),
            ~sa.exists().where(
                sa.and_(
                    arena_ai_batch_jobs.c.submission_id == arena_submissions.c.id,
                    arena_ai_batch_jobs.c.local_status.not_in(ARENA_AI_BATCH_JOB_TERMINAL_STATUSES),
                )
            ),
        )
        .order_by(arena_submissions.c.updated_at.asc())
        .limit(limit)
    )
    rows = (await conn.execute(stmt)).mappings().all()
    return [
        StuckAIReviewSubmission(
            submission_id=str(r["id"]),
            user_id=str(r["user_id"]),
            problem_id=str(r["problem_id"]),
            language_id=str(r["language_id"]),
            use_platform_key=bool(r["use_platform_key"]),
        )
        for r in rows
    ]


async def get_submission_for_review(conn: AsyncConnection, submission_id: str) -> SubmissionForReview | None:
    """Fetch submission fields required for AI review.

    LEFT JOINs with ``arena_submission_ai_reviews`` to provide the idempotency
    guard (``already_reviewed``) in a single round-trip.

    Args:
        conn: Active async database connection.
        submission_id: UUID string of the arena_submissions record.

    Returns:
        SubmissionForReview dataclass, or None when not found.
    """
    stmt = (
        sa.select(
            arena_submissions.c.id,
            arena_submissions.c.source_code,
            arena_submissions.c.user_id,
            arena_submissions.c.problem_id,
            arena_problems.c.arena_number,
            arena_problems.c.title,
            arena_submissions.c.language_id,
            arena_submission_ai_reviews.c.submission_id.label("review_submission_id"),
        )
        .select_from(
            arena_submissions.join(
                arena_problems,
                arena_submissions.c.problem_id == arena_problems.c.id,
            ).outerjoin(
                arena_submission_ai_reviews,
                arena_submissions.c.id == arena_submission_ai_reviews.c.submission_id,
            )
        )
        .where(arena_submissions.c.id == submission_id)
    )

    row = (await conn.execute(stmt)).mappings().first()
    if row is None:
        return None

    return SubmissionForReview(
        submission_id=str(row["id"]),
        source_code=str(row["source_code"]),
        user_id=str(row["user_id"]),
        problem_id=str(row["problem_id"]),
        problem_number=int(row["arena_number"]),
        problem_title=str(row["title"]),
        language_id=str(row["language_id"]),
        already_reviewed=row["review_submission_id"] is not None,
    )


async def get_user_api_key(conn: AsyncConnection, user_id: str) -> str | None:
    """Return the decrypted AI provider API key for the given user.

    The ``EncryptedString`` TypeDecorator on the ``_ai_api_key`` column
    performs decryption transparently during the SELECT.

    Args:
        conn: Active async database connection.
        user_id: UUID string of the arena_users record.

    Returns:
        Decrypted API key string, or None when not set.
    """
    stmt = sa.select(arena_users.c["_ai_api_key"]).where(arena_users.c.id == user_id)
    row = (await conn.execute(stmt)).first()
    if row is None:
        return None
    value = row[0]
    return str(value) if value is not None else None


async def get_user_prefered_language(conn: AsyncConnection, user_id: str) -> str:
    """Return the user's preferred locale for AI review responses.

    Args:
        conn: Active async database connection.
        user_id: UUID string of the arena_users record.

    Returns:
        Stored locale, or ``"en-US"`` when the user row is missing.
    """
    stmt = sa.select(arena_users.c.prefered_language).where(arena_users.c.id == user_id)
    row = (await conn.execute(stmt)).first()
    if row is None or row[0] is None:
        return "en-US"
    value = str(row[0])
    return value if value in {"en-US", "pt-BR"} else "en-US"


async def get_problem_data(conn: AsyncConnection, problem_id: str) -> ProblemData:
    """Return the problem statement and image data for the given problem.

    Args:
        conn: Active async database connection.
        problem_id: UUID string of the arena_problems record.

    Returns:
        ProblemData with statement text and optional image fields.
        All fields are None when the problem is not found.
    """
    stmt = sa.select(
        arena_problems.c.problem_statement,
        arena_problems.c.problem_image_base64,
        arena_problems.c.problem_image_mime,
        arena_problems.c.problem_image_caption,
    ).where(arena_problems.c.id == problem_id)
    row = (await conn.execute(stmt)).mappings().first()
    if row is None:
        return ProblemData(
            problem_statement=None,
            image_base64=None,
            image_mime=None,
            image_caption=None,
        )
    return ProblemData(
        problem_statement=str(row["problem_statement"]) if row["problem_statement"] else None,
        image_base64=str(row["problem_image_base64"]) if row["problem_image_base64"] else None,
        image_mime=str(row["problem_image_mime"]) if row["problem_image_mime"] else None,
        image_caption=str(row["problem_image_caption"]) if row["problem_image_caption"] else None,
    )


async def store_ai_review_result(
    conn: AsyncConnection,
    submission_id: str,
    response_text: str,
    response_at: datetime,
    cost_micros: int | None,
    used_platform_key: bool,
) -> None:
    """Insert the AI review result into ``arena_submission_ai_reviews``.

    Uses ``INSERT ... ON CONFLICT DO NOTHING`` so a duplicate call (e.g. from
    the reaper re-enqueuing a job that already completed) is silently ignored.

    Args:
        conn: Active async database connection (within a transaction).
        submission_id: UUID string of the arena_submissions record.
        response_text: Text response from the AI provider.
        response_at: Timestamp when the response was received.
        cost_micros: Review cost as integer micros (value × 1_000_000), or None
            when token usage data was not returned by the API.
        used_platform_key: True when the platform API key was used; False when
            the user's own API key was used.
    """
    stmt = (
        pg_insert(arena_submission_ai_reviews)
        .values(
            submission_id=submission_id,
            ai_response=response_text,
            ai_response_at=response_at,
            _ai_review_cost=cost_micros,
            used_platform_key=used_platform_key,
        )
        .on_conflict_do_nothing(index_elements=["submission_id"])
    )
    await conn.execute(stmt)


async def store_ai_review_completed_notification(
    conn: AsyncConnection,
    submission: SubmissionForReview,
) -> None:
    """Insert the user notification for a completed AI review.

    Args:
        conn: Active async database connection (within a transaction).
        submission: Submission metadata for the completed AI review.
    """
    await create_arena_notification(
        conn,
        user_id=submission.user_id,
        notification_kind=ArenaNotificationKind.AI_REVIEW_COMPLETED,
        title="AI review completed",
        message=(
            f"Your AI review for problem {submission.problem_number} - "
            f"{submission.problem_title} is ready. View the feedback."
        ),
        target_url=f"/submissions/{submission.submission_id}",
        source_ref=submission.submission_id,
        context={
            "submission_id": submission.submission_id,
            "problem_id": submission.problem_id,
            "problem_number": submission.problem_number,
        },
    )


async def store_ai_review_stale_notification(
    conn: AsyncConnection,
    submission: SubmissionForReview,
) -> None:
    """Insert the user notification for a stale (locally expired) AI review.

    Sent when a platform-funded batch review never reached a terminal status and
    was expired locally. The consumed credit is refunded separately; this message
    tells the user the review timed out and that they may request it again.

    Args:
        conn: Active async database connection (within a transaction).
        submission: Submission metadata for the stale AI review.
    """
    await create_arena_notification(
        conn,
        user_id=submission.user_id,
        notification_kind=ArenaNotificationKind.AI_REVIEW_FAILED,
        title="AI review timed out",
        message=(
            f"Your AI review for problem {submission.problem_number} - "
            f"{submission.problem_title} did not complete in time. Your credit was "
            f"refunded — you can request the review again."
        ),
        target_url=f"/submissions/{submission.submission_id}",
        source_ref=submission.submission_id,
        context={
            "submission_id": submission.submission_id,
            "problem_id": submission.problem_id,
            "problem_number": submission.problem_number,
        },
    )


async def clear_submit_to_ai_flag(conn: AsyncConnection, submission_id: str) -> None:
    """Reset the submit_to_ai flag on a submission row to False.

    Clears the pending-review indicator so the UI no longer shows "review
    pending" and the user is able to re-request a review once their API key
    is fixed.

    Args:
        conn: Active async database connection (within a transaction).
        submission_id: UUID string of the arena_submissions record.
    """
    stmt = sa.update(arena_submissions).where(arena_submissions.c.id == submission_id).values(submit_to_ai=False)
    await conn.execute(stmt)


async def store_ai_review_failed_notification(
    conn: AsyncConnection,
    submission: SubmissionForReview,
    is_user_key: bool,
) -> None:
    """Insert the user notification for a failed AI review.

    Sends a permanent-failure notification to the user.  The message content
    differs depending on whether the invalid key belongs to the user or to the
    platform:

    - User-owned key: instructs the user to update their key in profile settings.
    - Platform key: informs the user the review failed, that their credit was
      refunded, and that they may request the review again.

    Args:
        conn: Active async database connection (within a transaction).
        submission: Submission metadata for the failed AI review.
        is_user_key: True when the request used the user's own API key;
            False when it used the platform key.
    """
    if is_user_key:
        title = "AI review failed"
        message = "Your OpenAI API key is invalid. Please update it in your profile settings."
    else:
        title = "AI review credit refunded"
        message = (
            f"Your AI review for problem {submission.problem_number} - "
            f"{submission.problem_title} could not be completed due to a service issue. "
            f"Your credit has been refunded — you can request the review again."
        )

    await create_arena_notification(
        conn,
        user_id=submission.user_id,
        notification_kind=ArenaNotificationKind.AI_REVIEW_FAILED,
        title=title,
        message=message,
        target_url=f"/submissions/{submission.submission_id}",
        source_ref=submission.submission_id,
        context={
            "submission_id": submission.submission_id,
            "problem_id": submission.problem_id,
            "problem_number": submission.problem_number,
        },
    )
