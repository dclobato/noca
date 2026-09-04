#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The AI-review request workflow behind ``POST /submissions/{id}/request-ai-review``.

Three decisions live here rather than in the route, and each is deliberate:

- **A repeat request never re-enqueues.** Once ``submit_to_ai`` is set, the
  answer is :attr:`AIReviewRequestOutcome.PENDING` and nothing is pushed. The
  route used to "self-heal" a job lost after commit by enqueueing again on
  every call -- with no dedupe on ``ai:queue:pending`` that was a free way to
  push thousands of duplicate jobs, each costing an OpenAI call when dequeued,
  and it re-derived ``use_platform_key`` from the user's *current* key rather
  than the frozen one. Recovery of a genuinely lost job belongs to the
  ``aiassistant`` reconciler alone: it applies the complete predicate (flagged,
  no review row, no active batch job, absent from pending and inflight, past a
  grace window) and freezes the key mode from the database.
- **The first request is serialized on the submission row.** The row is read
  ``SELECT ... FOR UPDATE`` before its state is judged or a credit consumed, so
  two overlapping first requests cannot both pass the ``submit_to_ai=False``
  check: the second blocks, then sees the flag and takes the pending path. The
  lock is a PostgreSQL row lock, global across replicas, and a no-op on SQLite.
  No Valkey ``SET NX`` guard is layered on top -- it would be a weaker, fail-open,
  TTL-scoped copy of a control the database already provides.
- **Requests are capped per user, not per IP**, through the shared keyed
  fixed window (bucket ``arena:ai-review``), counted before any lookup so a
  flood does no database work. ``arena/services/rate_limit_service.py`` counts
  ``arena_submissions`` rows and cannot express this budget.
"""

from __future__ import annotations

from enum import StrEnum

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.models.arena_users import ArenaUser
from arena.services.user_ai_credit_service import consume_ai_credit
from shared.db_schema.arena import arena_submission_ai_reviews, arena_submissions
from shared.queue_schema import ArenaAIReviewJob
from shared.services.request_rate_limit import (
    InMemoryRateLimiter,
    RateLimitPolicy,
    check_rate_limit,
)
from shared.services.valkey_service import enqueue_arena_ai_review_job

__all__ = [
    "AI_REVIEW_RATE_LIMIT_BUCKET",
    "AI_REVIEW_RATE_LIMITER",
    "AIReviewRequestOutcome",
    "ai_review_request_verdict",
    "request_ai_review",
]

AI_REVIEW_RATE_LIMIT_BUCKET = "arena:ai-review"
AI_REVIEW_RATE_LIMITER = InMemoryRateLimiter()


class AIReviewRequestOutcome(StrEnum):
    """What one request did, for the route to translate into a flash and redirect."""

    NOT_FOUND = "not_found"
    """No such submission, or not owned by the caller."""
    COMPLETED = "completed"
    """A review already exists; nothing to do."""
    PENDING = "pending"
    """Already flagged; the worker (or the reconciler) owns it. Nothing enqueued."""
    INSUFFICIENT_CREDIT = "insufficient_credit"
    """No personal key and no platform credit left; nothing written."""
    ENQUEUED = "enqueued"
    """Flag set, credit consumed when applicable, committed, and one job pushed."""


def _rate_limit_policy() -> RateLimitPolicy:
    """Build the per-user AI review request policy from the current settings."""
    return RateLimitPolicy(
        bucket=AI_REVIEW_RATE_LIMIT_BUCKET,
        max_requests=settings.AI_REVIEW_RATE_LIMIT_MAX_REQUESTS,
        window_seconds=settings.AI_REVIEW_RATE_LIMIT_WINDOW_SECONDS,
        enabled=settings.AI_REVIEW_RATE_LIMIT_ENABLED,
    )


async def ai_review_request_verdict(request: Request, user_id: str) -> tuple[bool, int]:
    """Count one AI review request for ``user_id`` and say whether it may proceed.

    Returns:
        ``(allowed, retry_after_seconds)``.
    """
    return await check_rate_limit(
        request, policy=_rate_limit_policy(), fallback_limiter=AI_REVIEW_RATE_LIMITER, key=user_id
    )


def submission_lookup(submission_id: str):  # type: ignore[no-untyped-def]
    """The locked lookup the workflow starts with; exposed so tests can inspect it."""
    return (
        select(
            arena_submissions.c.user_id,
            arena_submissions.c.problem_id,
            arena_submissions.c.language_id,
            arena_submissions.c.submit_to_ai,
        )
        .where(arena_submissions.c.id == submission_id)
        .with_for_update()
    )


async def request_ai_review(
    session: AsyncSession,
    valkey_runtime: object,
    user: ArenaUser,
    submission_id: str,
) -> AIReviewRequestOutcome:
    """Run the request workflow for ``user`` on ``submission_id``.

    Args:
        session: Active database session; committed here on the enqueue path.
        valkey_runtime: The process Valkey runtime the job is pushed through.
        user: The authenticated requester (must own the submission).
        submission_id: UUID of the ``arena_submissions`` row.

    Returns:
        The outcome; see :class:`AIReviewRequestOutcome`.
    """
    row = (await session.execute(submission_lookup(submission_id))).one_or_none()
    if row is None or row[0] != user.id:
        return AIReviewRequestOutcome.NOT_FOUND

    review_exists = (
        await session.scalar(
            select(arena_submission_ai_reviews.c.submission_id).where(
                arena_submission_ai_reviews.c.submission_id == submission_id
            )
        )
    ) is not None
    if review_exists:
        return AIReviewRequestOutcome.COMPLETED

    if row[3]:  # submit_to_ai already set: recovery, if ever needed, is the reconciler's
        return AIReviewRequestOutcome.PENDING

    # The key-mode decision is frozen here so the worker never re-derives it.
    use_platform_key = not bool(user.ai_api_key)
    if use_platform_key:
        consumed = await consume_ai_credit(user, session, submission_id=submission_id)
        if not consumed:
            return AIReviewRequestOutcome.INSUFFICIENT_CREDIT

    await session.execute(
        update(arena_submissions).where(arena_submissions.c.id == submission_id).values(submit_to_ai=True)
    )
    job = ArenaAIReviewJob(
        submission_id=submission_id,
        user_id=row[0],
        problem_id=row[1],
        language_id=row[2],
        use_platform_key=use_platform_key,
    )
    # Commit first: a crash between here and the push leaves a flagged row the
    # reconciler recovers; the reverse order would lose the charge instead.
    await session.commit()
    await enqueue_arena_ai_review_job(valkey_runtime, job)
    return AIReviewRequestOutcome.ENQUEUED
