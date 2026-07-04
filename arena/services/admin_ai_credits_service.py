#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Admin-facing service for querying AI credit consumption across all Arena users."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from arena.models.arena_ai_credit_transactions import ArenaAiCreditTransaction
from arena.models.arena_submissions import ArenaSubmission, ArenaSubmissionAIReview
from arena.models.arena_users import ArenaUser
from arena.services.pagination_service import Pagination, PaginationParams, clamp_page
from shared.db_schema.arena import arena_ai_batch_jobs, arena_submission_ai_reviews


async def get_batch_job_statuses(
    session: AsyncSession,
    submission_ids: set[str],
) -> dict[str, str]:
    """Return the batch job ``local_status`` for each submission that has one.

    Args:
        session: Active async database session.
        submission_ids: Submission identifiers on the current transaction page.

    Returns:
        Mapping from submission identifier to ``local_status`` string.
        Submissions without a batch job row are omitted.
    """
    if not submission_ids:
        return {}

    query = select(
        arena_ai_batch_jobs.c.submission_id,
        arena_ai_batch_jobs.c.local_status,
    ).where(arena_ai_batch_jobs.c.submission_id.in_(submission_ids))
    rows = (await session.execute(query)).all()
    return {str(submission_id): str(local_status) for submission_id, local_status in rows}


async def get_batch_job_errors(
    session: AsyncSession,
    submission_ids: set[str],
) -> dict[str, str]:
    """Return the ``last_error`` for batch jobs that have one.

    Args:
        session: Active async database session.
        submission_ids: Submission identifiers on the current transaction page.

    Returns:
        Mapping from submission identifier to ``last_error`` string.
        Submissions without a batch job row or with a null ``last_error`` are omitted.
    """
    if not submission_ids:
        return {}

    query = select(
        arena_ai_batch_jobs.c.submission_id,
        arena_ai_batch_jobs.c.last_error,
    ).where(
        arena_ai_batch_jobs.c.submission_id.in_(submission_ids),
        arena_ai_batch_jobs.c.last_error.isnot(None),
    )
    rows = (await session.execute(query)).all()
    return {str(submission_id): str(last_error) for submission_id, last_error in rows}


async def get_refunded_submission_ids(
    session: AsyncSession,
    submission_ids: set[str],
) -> set[str]:
    """Return submission IDs on the page that have a matching refund transaction.

    Args:
        session: Active async database session.
        submission_ids: Submission identifiers on the current transaction page.

    Returns:
        Subset of ``submission_ids`` where a ``refund`` credit transaction exists.
    """
    if not submission_ids:
        return set()

    query = select(ArenaAiCreditTransaction.submission_id).where(
        ArenaAiCreditTransaction.submission_id.in_(submission_ids),
        ArenaAiCreditTransaction.transaction_type == "refund",
    )
    rows = (await session.execute(query)).scalars().all()
    return {str(sid) for sid in rows}


async def get_batch_turnaround_seconds(
    session: AsyncSession,
    submission_ids: set[str],
) -> dict[str, int]:
    """Return batch-staging-to-review-storage durations by submission.

    Args:
        session: Active async database session.
        submission_ids: Submission identifiers on the current transaction page.

    Returns:
        Mapping from submission identifier to non-negative whole elapsed seconds.
        Personal-key reviews and rows without both timestamps are omitted.
    """
    if not submission_ids:
        return {}

    query = (
        select(
            arena_ai_batch_jobs.c.submission_id,
            arena_ai_batch_jobs.c.created_at,
            arena_submission_ai_reviews.c.ai_response_at,
        )
        .join(
            arena_submission_ai_reviews,
            arena_submission_ai_reviews.c.submission_id == arena_ai_batch_jobs.c.submission_id,
        )
        .where(
            arena_ai_batch_jobs.c.submission_id.in_(submission_ids),
            arena_submission_ai_reviews.c.used_platform_key.is_(True),
        )
    )
    rows = (await session.execute(query)).all()
    return {
        str(submission_id): max(0, int((response_at - batch_created_at).total_seconds()))
        for submission_id, batch_created_at, response_at in rows
    }


async def list_consumption_transactions_paginated(
    session: AsyncSession,
    *,
    page: int,
    per_page: int,
    search: str = "",
    sort_dir: str = "desc",
    date_from_utc: datetime | None = None,
    date_to_utc: datetime | None = None,
) -> Pagination[ArenaAiCreditTransaction]:
    """Return a paginated list of AI credit consumption transactions across all users.

    Args:
        session: Active async database session.
        page: Requested page number (one-based).
        per_page: Number of items per page.
        search: Optional string matched against user full name and normalised email.
        sort_dir: 'asc' for oldest-first; any other value gives newest-first.
        date_from_utc: Inclusive lower bound (UTC datetime); filters created_at >= value.
        date_to_utc: Exclusive upper bound (UTC datetime); filters created_at < value.

    Returns:
        Pagination[ArenaAiCreditTransaction]: Page of consumption rows with user,
            submission, and ai_review relationships eager-loaded.
    """
    conditions: list[ColumnElement[bool]] = [
        ArenaAiCreditTransaction.transaction_type == "consumption",
    ]

    if search.strip():
        term = f"%{search.strip()}%"
        conditions.append(
            ArenaAiCreditTransaction.user_id.in_(
                select(ArenaUser.id).where(
                    or_(
                        ArenaUser.nome.ilike(term),
                        ArenaUser.email_normalizado.ilike(term),
                    )
                )
            )
        )

    if date_from_utc is not None:
        conditions.append(ArenaAiCreditTransaction.created_at >= date_from_utc)

    if date_to_utc is not None:
        conditions.append(ArenaAiCreditTransaction.created_at < date_to_utc)

    count_query = select(func.count()).select_from(ArenaAiCreditTransaction).where(*conditions)
    total: int = (await session.execute(count_query)).scalar() or 0

    effective_page = clamp_page(page, total=total, per_page=per_page)
    params = PaginationParams(page=effective_page, per_page=per_page)

    if sort_dir == "asc":
        order_cols = (
            ArenaAiCreditTransaction.created_at.asc(),
            ArenaAiCreditTransaction.id.asc(),
        )
    else:
        order_cols = (
            ArenaAiCreditTransaction.created_at.desc(),
            ArenaAiCreditTransaction.id.desc(),
        )

    data_query = (
        select(ArenaAiCreditTransaction)
        .options(
            selectinload(ArenaAiCreditTransaction.user),
            selectinload(ArenaAiCreditTransaction.submission).selectinload(
                ArenaSubmission.ai_review.of_type(ArenaSubmissionAIReview)
            ),
        )
        .where(*conditions)
        .order_by(*order_cols)
        .offset(params.offset)
        .limit(params.per_page)
    )
    items = list((await session.execute(data_query)).scalars().all())

    return Pagination(items=items, page=effective_page, per_page=per_page, total=total)
