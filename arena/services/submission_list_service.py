#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Service for listing Arena submissions on the user profile page and the admin list."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, String, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.services.pagination_service import Pagination, PaginationParams, clamp_page
from shared.db_schema import languages as languages_table
from shared.db_schema.arena import (
    arena_problems,
    arena_submission_ai_reviews,
    arena_submission_judgments,
    arena_submission_teacher_feedback,
    arena_submissions,
    arena_users,
)
from shared.enumerations import TERMINAL_JUDGMENT_STATUSES
from shared.services.arena_query_helpers import active_arena_judgment_subquery

# Page size for the profile submissions tab. Shared so the realtime status
# endpoints cap the number of watchable IDs at exactly what the page can render
# (a larger page must never produce more IDs than the endpoints accept).
ARENA_SUBMISSIONS_PER_PAGE = 25


@dataclass(frozen=True)
class SubmissionListRow:
    """One row shown in the user's submission history list.

    Attributes:
        submission_id: UUID of the arena submission.
        problem_id: UUID of the arena problem.
        problem_number: Public sequential problem number.
        problem_title: Problem display title.
        language_id: Language key (e.g. 'python3').
        language_name: Language display name.
        language_icon: Devicon CSS class string.
        submitted_at: Timestamp of the submission.
        verdict: ``final_verdict`` string from the active judgment, or ``None``.
        status: ``JudgmentStatus`` string of the active judgment, or ``None``.
        max_wall_time_ms: Worst wall-clock time in milliseconds, or ``None``.
        submit_to_ai: ``True`` when the submission has been queued for AI review.
        has_ai_review: ``True`` when a completed AI review exists for this submission.
        has_teacher_feedback: ``True`` when a teacher left feedback on this submission.
        is_final: ``True`` when the active judgment status is terminal (``DONE``,
            ``FAILED``, or ``SUPERSEDED``). ``FAILED`` and ``SUPERSEDED`` are final
            without a verdict, so this is the authoritative "stop watching" signal.
    """

    submission_id: str
    problem_id: str
    problem_number: int
    problem_title: str
    language_id: str
    language_name: str
    language_icon: str
    submitted_at: datetime
    verdict: str | None
    status: str | None
    max_wall_time_ms: int | None
    submit_to_ai: bool
    has_ai_review: bool
    has_teacher_feedback: bool
    is_final: bool


def build_arena_submission_query(
    *,
    user_id: str | None = None,
    id_filter: Sequence[str] | None = None,
    problem_search: str | None = None,
    user_search: str | None = None,
    verdict_filter: str | None = None,
    status_filter: str | None = None,
    ai_filter: str | None = None,
    language_filter: str | None = None,
    problem_filter: str | None = None,
    date_from_utc: datetime | None = None,
    date_to_utc: datetime | None = None,
    sort_dir: str = "desc",
    include_user: bool = False,
) -> Select[Any]:
    """Build a configurable Arena submission list query.

    Joins ``arena_submissions`` → ``arena_problems`` → ``languages`` → active judgment
    → ``arena_submission_ai_reviews`` (left) → ``arena_submission_teacher_feedback`` (left).
    When ``include_user=True``, also joins ``arena_users`` and appends
    ``arena_users.c.nome`` (index 14) and ``arena_users.c.id`` (index 15) after
    all existing columns so existing positional unpacking in callers that use
    ``include_user=False`` remains unchanged.

    Args:
        user_id: When set, restricts results to this Arena user's submissions.
        id_filter: When not ``None``, restricts results to these submission IDs via
            ``IN``. An empty sequence intentionally matches **no** rows; pass ``None``
            to disable the filter entirely.
        problem_search: ilike match against problem title or cast(arena_number).
        user_search: ilike match against arena_users.nome or email_normalizado.
            Only evaluated when ``include_user=True``.
        verdict_filter: Exact ``final_verdict`` match (e.g. ``"AC"``).
        status_filter: Exact active ``JudgmentStatus`` match (e.g. ``"FAILED"``).
        ai_filter: ``"yes"`` → ``submit_to_ai=True``; ``"no"`` → ``False``; else no filter.
            Filters the ``submit_to_ai`` column on the submission itself, not review presence.
        language_filter: Exact ``language_id`` match.
        problem_filter: Exact match on cast(arena_number). Filtering by ``"10"`` matches
            only problem 10, not 100 or 210.
        date_from_utc: Inclusive UTC lower bound for ``created_at``.
        date_to_utc: Exclusive UTC upper bound for ``created_at``.
        sort_dir: ``"asc"`` for oldest first; any other value gives newest first.
        include_user: When ``True``, joins ``arena_users`` and appends nome + id columns.

    Returns:
        Select: SQLAlchemy select statement ready for count or paginated execution.
    """
    active_j = active_arena_judgment_subquery()

    from_clause = (
        arena_submissions.join(
            arena_problems,
            arena_submissions.c.problem_id == arena_problems.c.id,
        )
        .join(
            languages_table,
            arena_submissions.c.language_id == languages_table.c.id,
        )
        .outerjoin(
            active_j,
            active_j.c.submission_id == arena_submissions.c.id,
        )
        .outerjoin(
            arena_submission_judgments,
            and_(
                arena_submission_judgments.c.submission_id == arena_submissions.c.id,
                arena_submission_judgments.c.created_at == active_j.c.max_created_at,
            ),
        )
        .outerjoin(
            arena_submission_ai_reviews,
            arena_submission_ai_reviews.c.submission_id == arena_submissions.c.id,
        )
        .outerjoin(
            arena_submission_teacher_feedback,
            arena_submission_teacher_feedback.c.submission_id == arena_submissions.c.id,
        )
    )

    columns = [
        arena_submissions.c.id,  # 0
        arena_problems.c.id,  # 1
        arena_problems.c.arena_number,  # 2
        arena_problems.c.title,  # 3
        arena_submissions.c.language_id,  # 4
        languages_table.c.name,  # 5
        languages_table.c.icon,  # 6
        arena_submissions.c.created_at,  # 7
        arena_submission_judgments.c.final_verdict,  # 8
        arena_submission_judgments.c.status,  # 9
        arena_submission_judgments.c.max_wall_time_ms,  # 10
        arena_submissions.c.submit_to_ai,  # 11
        arena_submission_ai_reviews.c.submission_id,  # 12
        arena_submission_teacher_feedback.c.submission_id,  # 13
    ]

    if include_user:
        from_clause = from_clause.join(
            arena_users,
            arena_submissions.c.user_id == arena_users.c.id,
        )
        columns.append(arena_users.c.nome)  # 14
        columns.append(arena_users.c.id)  # 15

    stmt = select(*columns).select_from(from_clause)

    if user_id is not None:
        stmt = stmt.where(arena_submissions.c.user_id == user_id)

    if id_filter is not None:
        stmt = stmt.where(arena_submissions.c.id.in_(id_filter))

    if problem_search:
        term = f"%{problem_search}%"
        stmt = stmt.where(arena_problems.c.title.ilike(term) | cast(arena_problems.c.arena_number, String).ilike(term))

    if user_search and include_user:
        term = f"%{user_search}%"
        stmt = stmt.where(arena_users.c.nome.ilike(term) | arena_users.c.email_normalizado.ilike(term))

    if verdict_filter:
        stmt = stmt.where(arena_submission_judgments.c.final_verdict == verdict_filter)

    if status_filter:
        stmt = stmt.where(arena_submission_judgments.c.status == status_filter)

    if ai_filter == "yes":
        stmt = stmt.where(arena_submissions.c.submit_to_ai.is_(True))
    elif ai_filter == "no":
        stmt = stmt.where(arena_submissions.c.submit_to_ai.is_(False))

    if language_filter:
        stmt = stmt.where(arena_submissions.c.language_id == language_filter)

    if problem_filter:
        stmt = stmt.where(cast(arena_problems.c.arena_number, String) == problem_filter)

    if date_from_utc is not None:
        stmt = stmt.where(arena_submissions.c.created_at >= date_from_utc)

    if date_to_utc is not None:
        stmt = stmt.where(arena_submissions.c.created_at < date_to_utc)

    if sort_dir == "asc":
        return stmt.order_by(arena_submissions.c.created_at.asc(), arena_submissions.c.id.asc())
    return stmt.order_by(arena_submissions.c.created_at.desc(), arena_submissions.c.id.desc())


async def get_user_submissions(
    *,
    session: AsyncSession,
    user_id: str,
    search: str | None = None,
    verdict_filter: str | None = None,
    params: PaginationParams,
) -> Pagination[SubmissionListRow]:
    """Return a paginated list of Arena submissions for one user.

    Args:
        session: Active async database session.
        user_id: Arena user UUID whose submissions are returned.
        search: Optional partial match string for problem title or number.
        verdict_filter: Optional exact ``final_verdict`` value (e.g. ``"AC"``).
            Pass ``None`` or an empty string to skip filtering by verdict.
        params: Pagination settings (page, per_page).

    Returns:
        Pagination[SubmissionListRow]: Paginated submission rows, newest first.
    """
    base = build_arena_submission_query(
        user_id=user_id,
        problem_search=search or None,
        verdict_filter=verdict_filter or None,
    )

    count_stmt = select(func.count()).select_from(base.order_by(None).subquery())
    total: int = await session.scalar(count_stmt) or 0

    page = clamp_page(params.page, total=total, per_page=params.per_page)
    rows = (await session.execute(base.limit(params.per_page).offset((page - 1) * params.per_page))).all()

    items = [
        SubmissionListRow(
            submission_id=row[0],
            problem_id=row[1],
            problem_number=row[2],
            problem_title=row[3],
            language_id=row[4],
            language_name=row[5],
            language_icon=row[6],
            submitted_at=row[7],
            verdict=row[8],
            status=row[9],
            max_wall_time_ms=row[10],
            submit_to_ai=row[11],
            has_ai_review=row[12] is not None,
            has_teacher_feedback=row[13] is not None,
            is_final=row[9] in TERMINAL_JUDGMENT_STATUSES,
        )
        for row in rows
    ]
    return Pagination(items=items, page=page, per_page=params.per_page, total=total)
