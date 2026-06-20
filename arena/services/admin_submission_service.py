#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Admin-facing service for browsing all Arena submissions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.services.pagination_service import Pagination, clamp_page
from arena.services.submission_list_service import build_arena_submission_query


@dataclass(frozen=True)
class AdminSubmissionListRow:
    """One row shown in the admin submission list.

    Attributes:
        submission_id: UUID of the arena submission.
        user_id: UUID of the submitting Arena user.
        user_name: Display name of the submitting user.
        problem_number: Public sequential problem number.
        problem_title: Problem display title.
        language_id: Language key (e.g. 'python3').
        language_name: Language display name.
        submitted_at: Timestamp of the submission.
        verdict: ``final_verdict`` from the active judgment, or ``None`` when pending.
        status: ``JudgmentStatus`` string of the active judgment, or ``None``.
        submit_to_ai: ``True`` when the submission was queued for AI review.
        has_ai_review: ``True`` when a completed AI review row exists.
    """

    submission_id: str
    user_id: str
    user_name: str
    problem_number: int
    problem_title: str
    language_id: str
    language_name: str
    submitted_at: datetime
    verdict: str | None
    status: str | None
    submit_to_ai: bool
    has_ai_review: bool


_ALLOWED_PER_PAGE: set[int] = {10, 25, 50, 100, 500}
_DEFAULT_PER_PAGE = 25


async def list_submissions_paginated(
    session: AsyncSession,
    *,
    page: int,
    per_page: int,
    search: str = "",
    verdict_filter: str | None = None,
    ai_filter: str | None = None,
    language_filter: str | None = None,
    problem_filter: str | None = None,
    sort_dir: str = "desc",
    date_from_utc: datetime | None = None,
    date_to_utc: datetime | None = None,
) -> Pagination[AdminSubmissionListRow]:
    """Return a paginated list of all Arena submissions with optional filters.

    Delegates to ``build_arena_submission_query`` with ``include_user=True`` so
    user columns appear at indices 14 (nome) and 15 (user id).

    Args:
        session: Active async database session.
        page: Requested page number, starting at one.
        per_page: Number of records per page.
        search: ilike match against ``arena_users.nome`` or ``email_normalizado``.
        verdict_filter: Exact ``final_verdict`` value (e.g. ``"WA"``).
        ai_filter: ``"yes"`` → ``submit_to_ai=True``; ``"no"`` → ``False``; else all.
        language_filter: Exact ``language_id`` match.
        problem_filter: Exact match on cast(arena_number). ``"10"`` matches only
            problem 10, not 100 or 210.
        sort_dir: ``"asc"`` for oldest first; any other value gives newest first.
        date_from_utc: Inclusive UTC lower bound for the submission timestamp.
        date_to_utc: Exclusive UTC upper bound for the submission timestamp.

    Returns:
        Pagination[AdminSubmissionListRow]: Paginated submission rows in ``sort_dir`` order.
    """
    base = build_arena_submission_query(
        user_search=search or None,
        verdict_filter=verdict_filter or None,
        ai_filter=ai_filter or None,
        language_filter=language_filter or None,
        problem_filter=problem_filter or None,
        date_from_utc=date_from_utc,
        date_to_utc=date_to_utc,
        sort_dir=sort_dir,
        include_user=True,
    )

    count_stmt = select(func.count()).select_from(base.order_by(None).subquery())
    total: int = await session.scalar(count_stmt) or 0

    effective_page = clamp_page(page, total=total, per_page=per_page)
    rows = (await session.execute(base.limit(per_page).offset((effective_page - 1) * per_page))).all()

    items = [
        AdminSubmissionListRow(
            submission_id=row[0],
            user_id=row[15],
            user_name=row[14],
            problem_number=row[2],
            problem_title=row[3],
            language_id=row[4],
            language_name=row[5],
            submitted_at=row[7],
            verdict=row[8],
            status=row[9],
            submit_to_ai=row[11],
            has_ai_review=row[12] is not None,
        )
        for row in rows
    ]
    return Pagination(items=items, page=effective_page, per_page=per_page, total=total)
