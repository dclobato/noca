#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin service for problem CRUD, search, filtering, and pagination.

Access-control pattern:
  - ``is_admin=True``: no owner restriction; caller sees all problems.
  - ``is_admin=False``: queries are scoped to ``caller_id`` only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Select, cast, func, or_, select
from sqlalchemy import String as SAString
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from arena.models.arena_problems import ArenaCategory, ArenaProblem, ArenaRatingProblem
from arena.models.arena_users import ArenaUser
from arena.services.pagination_service import Pagination, PaginationParams
from shared.db_schema.arena import arena_problem_category_map as _cat_map_table
from shared.db_schema.arena import arena_submissions as _arena_submissions
from shared.enumerations import ArenaRole, JudgmentStatus
from shared.problem_statement_markdown import validate_md_content
from shared.queue_schema import ArenaSubmissionJob

_DEFAULT_TIME_LIMIT_MS = 1000
_DEFAULT_MEMORY_LIMIT_KB = 262144
_DEFAULT_PIDS_LIMIT = 64
_DEFAULT_OUTPUT_LIMIT_BYTES = 65536
_MAX_TITLE_LEN = 256
_MAX_SOURCE_LEN = 256
_MAX_AUTHOR_LEN = 80
_MAX_LICENSE_LEN = 256

_VALID_SORT_KEYS = {
    "title_asc",
    "title_desc",
    "number_asc",
    "number_desc",
    "rating_asc",
    "rating_desc",
}


@dataclass(frozen=True)
class ProblemListItem:
    """Single row in the admin problem list page."""

    problem: ArenaProblem
    public_tc_count: int
    private_tc_count: int
    rating: float | None
    categories: list[ArenaCategory]


def _now() -> datetime:
    return datetime.now(UTC)


def _validate_problem_data(
    title: str,
    source: str | None,
    author: str | None,
    author_is_owner: bool,
    license: str | None,
    time_limit_ms: int,
    memory_limit_kb: int,
    pids_limit: int,
    output_limit_in_bytes: int,
    problem_statement: str,
) -> None:
    """Validate problem form data and raise ValueError on any violation.

    Image validation is handled upstream by ``ImageProcessingService`` before
    the service is called; only scalar fields are validated here.
    """
    if not title.strip():
        raise ValueError("Title is required.")
    if len(title) > _MAX_TITLE_LEN:
        raise ValueError(f"Title must be at most {_MAX_TITLE_LEN} characters.")
    if source and len(source) > _MAX_SOURCE_LEN:
        raise ValueError(f"Source must be at most {_MAX_SOURCE_LEN} characters.")
    normalized_author = author.strip() if author else ""
    if not author_is_owner and not normalized_author:
        raise ValueError("Author is required when 'I'm problem author' is not selected.")
    if len(normalized_author) > _MAX_AUTHOR_LEN:
        raise ValueError(f"Author must be at most {_MAX_AUTHOR_LEN} characters.")
    if license and len(license.strip()) > _MAX_LICENSE_LEN:
        raise ValueError(f"License must be at most {_MAX_LICENSE_LEN} characters.")
    if time_limit_ms < 100:
        raise ValueError("Time limit must be at least 100 ms.")
    if memory_limit_kb < 1024:
        raise ValueError("Memory limit must be at least 1024 KB.")
    if pids_limit < 1:
        raise ValueError("PIDs limit must be at least 1.")
    if output_limit_in_bytes < 1:
        raise ValueError("Output limit must be at least 1 byte.")
    if not problem_statement.strip():
        raise ValueError("Markdown statement cannot be empty.")
    md_errors = validate_md_content(problem_statement)
    if md_errors:
        raise ValueError(md_errors[0])


async def _set_categories(
    session: AsyncSession,
    problem: ArenaProblem,
    category_ids: list[str],
) -> None:
    """Replace category associations using direct SQL.

    Avoids accessing ``problem.categories`` directly (which would trigger a
    lazy-load on a freshly-flushed object and raise ``MissingGreenlet`` in
    async contexts).
    """
    await session.execute(_cat_map_table.delete().where(_cat_map_table.c.problem_id == problem.id))
    if category_ids:
        id_result = await session.execute(select(ArenaCategory.id).where(ArenaCategory.id.in_(category_ids)))
        valid_ids = list(id_result.scalars())
        if valid_ids:
            await session.execute(
                _cat_map_table.insert(),
                [{"problem_id": problem.id, "category_id": cid} for cid in valid_ids],
            )
    session.expire(problem, ["categories"])


def _apply_sort(stmt: Select[tuple[ArenaProblem]], sort_by: str) -> Select[tuple[ArenaProblem]]:
    """Append ORDER BY clause for the given sort key."""
    if sort_by == "title_desc":
        return stmt.order_by(func.lower(ArenaProblem.title).desc())
    if sort_by == "number_asc":
        return stmt.order_by(ArenaProblem.arena_number.asc())
    if sort_by == "number_desc":
        return stmt.order_by(ArenaProblem.arena_number.desc())
    if sort_by == "rating_asc":
        return stmt.order_by(ArenaRatingProblem.rating.asc().nulls_last())
    if sort_by == "rating_desc":
        return stmt.order_by(ArenaRatingProblem.rating.desc().nulls_first())
    # default: title_asc
    return stmt.order_by(func.lower(ArenaProblem.title).asc())


async def list_problems_paginated(
    session: AsyncSession,
    *,
    page: int,
    per_page: int,
    search: str = "",
    category_ids: list[str] | None = None,
    category_slugs: list[str] | None = None,
    owner_id: str | None = None,
    sort_by: str = "title_asc",
    caller_id: str,
    is_admin: bool,
) -> Pagination[ProblemListItem]:
    """Return a paginated list of problems with search and filter support.

    Args:
        session: Active async database session.
        page: 1-based page number.
        per_page: Number of items per page.
        search: Free-text search applied to arena_number, title, problem_statement, source.
        category_ids: Require ALL listed category IDs (AND semantics). None = no filter.
        category_slugs: Require ALL listed category slugs (AND semantics). None = no filter.
        owner_id: Restrict to a specific owner (admin-only filter). None = no filter.
        sort_by: One of the ``_VALID_SORT_KEYS`` values.
        caller_id: UUID of the requesting user.
        is_admin: When False, scopes the query to problems owned by ``caller_id``.

    Returns:
        Pagination[ProblemListItem]: Paginated result with problem rows.
    """
    effective_sort = sort_by if sort_by in _VALID_SORT_KEYS else "title_asc"
    params = PaginationParams(page=max(1, page), per_page=max(1, per_page))

    # LEFT OUTER JOIN on rating to support rating-based ordering and eager-load.
    # contains_eager tells SQLAlchemy the relationship is already loaded via the join.
    base = (
        select(ArenaProblem)
        .outerjoin(ArenaRatingProblem, ArenaProblem.id == ArenaRatingProblem.problem_id)
        .options(
            contains_eager(ArenaProblem.rating),
            selectinload(ArenaProblem.categories),
            selectinload(ArenaProblem.test_cases),
        )
    )

    if not is_admin:
        base = base.where(ArenaProblem.owner_id == caller_id)
    elif owner_id:
        base = base.where(ArenaProblem.owner_id == owner_id)

    if search.strip():
        term = f"%{search.strip()}%"
        base = base.where(
            or_(
                cast(ArenaProblem.arena_number, SAString).ilike(term),
                ArenaProblem.title.ilike(term),
                ArenaProblem.problem_statement.ilike(term),
                ArenaProblem.source.ilike(term),
                ArenaProblem.author.ilike(term),
            )
        )

    if category_slugs:
        effective_slugs = list(dict.fromkeys(slug.strip().lower() for slug in category_slugs if slug.strip()))
        if effective_slugs:
            # AND semantics: problem must have every selected category slug.
            sub = (
                select(func.count(_cat_map_table.c.category_id.distinct()))
                .select_from(
                    _cat_map_table.join(
                        ArenaCategory,
                        _cat_map_table.c.category_id == ArenaCategory.id,
                    )
                )
                .where(
                    _cat_map_table.c.problem_id == ArenaProblem.id,
                    ArenaCategory.slug.in_(effective_slugs),
                )
                .scalar_subquery()
            )
            base = base.where(sub == len(effective_slugs))
    elif category_ids:
        effective_ids = list(dict.fromkeys(category_ids))
        # AND semantics: problem must have every selected category
        sub = (
            select(func.count(_cat_map_table.c.category_id.distinct()))
            .where(
                _cat_map_table.c.problem_id == ArenaProblem.id,
                _cat_map_table.c.category_id.in_(effective_ids),
            )
            .scalar_subquery()
        )
        base = base.where(sub == len(effective_ids))

    # Count total before pagination
    count_stmt = select(func.count()).select_from(base.subquery())
    total: int = (await session.execute(count_stmt)).scalar_one()

    # Apply sort and paginate
    paginated = _apply_sort(base, effective_sort).offset(params.offset).limit(params.per_page)

    rows = list((await session.execute(paginated)).scalars())

    items: list[ProblemListItem] = []
    for problem in rows:
        public_tcs = sum(1 for tc in problem.test_cases if tc.is_sample)
        private_tcs = sum(1 for tc in problem.test_cases if not tc.is_sample)
        rating = problem.rating.display_rating if problem.rating else None
        items.append(
            ProblemListItem(
                problem=problem,
                public_tc_count=public_tcs,
                private_tc_count=private_tcs,
                rating=rating,
                categories=list(problem.categories),
            )
        )

    return Pagination(items=items, page=params.page, per_page=params.per_page, total=total)


async def get_problem(
    session: AsyncSession,
    problem_id: str,
    *,
    caller_id: str,
    is_admin: bool,
) -> ArenaProblem | None:
    """Fetch a single problem by UUID, with access control.

    Args:
        session: Active async database session.
        problem_id: UUID of the problem.
        caller_id: UUID of the requesting user.
        is_admin: When False, returns None if the problem belongs to another owner.

    Returns:
        ArenaProblem | None: The problem, or ``None`` if not found / not allowed.
    """
    stmt = (
        select(ArenaProblem)
        .where(ArenaProblem.id == problem_id)
        .options(
            selectinload(ArenaProblem.categories),
            selectinload(ArenaProblem.test_cases),
        )
    )
    if not is_admin:
        stmt = stmt.where(ArenaProblem.owner_id == caller_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_problem(
    session: AsyncSession,
    *,
    caller_id: str,
    title: str,
    source: str | None,
    hide_author_show_source: bool,
    time_limit_ms: int,
    memory_limit_kb: int,
    pids_limit: int,
    output_limit_in_bytes: int,
    problem_statement: str,
    image_b64: str | None,
    image_mime: str | None,
    image_caption: str | None,
    notes: str | None,
    category_ids: list[str],
    license: str | None = None,
    author: str | None = None,
    author_is_owner: bool = True,
) -> ArenaProblem:
    """Create a new Arena problem in the disabled state.

    ``arena_number`` is assigned by the PostgreSQL sequence
    ``arena_problem_arena_number_seq`` via the column ``server_default``.
    The problem is always created with ``enabled=False``.

    Image bytes must already be processed and base64-encoded by
    ``ImageProcessingService.process_upload_image`` before being passed here;
    the service stores ``image_b64`` and ``image_mime`` verbatim.

    Args:
        session: Active async database session.
        caller_id: UUID of the owner (current user).
        image_b64: Base64-encoded image string from ``ImageProcessingResult.imagem_base64``,
            or ``None`` if no image was uploaded.
        image_mime: MIME type from ``ImageProcessingResult.mime_type``, or ``None``.
        image_caption: Optional caption text to display below the image, or ``None``.
        All other args correspond to form fields.

    Returns:
        ArenaProblem: The newly created problem (after flush).

    Raises:
        ValueError: On any validation failure.
    """
    _validate_problem_data(
        title,
        source,
        author,
        author_is_owner,
        license,
        time_limit_ms,
        memory_limit_kb,
        pids_limit,
        output_limit_in_bytes,
        problem_statement,
    )

    now = _now()
    problem = ArenaProblem(
        id=str(uuid.uuid4()),
        title=title.strip(),
        owner_id=caller_id,
        author=None if author_is_owner else author.strip() if author else None,
        author_is_owner=author_is_owner,
        source=source.strip() if source else None,
        hide_author_show_source=hide_author_show_source,
        enabled=False,
        time_limit_ms=time_limit_ms,
        memory_limit_kb=memory_limit_kb,
        pids_limit=pids_limit,
        output_limit_in_bytes=output_limit_in_bytes,
        problem_statement=problem_statement,
        problem_image_base64=image_b64,
        problem_image_mime=image_mime if image_b64 else None,
        problem_image_caption=image_caption.strip() if image_caption else None,
        notes=notes.strip() if notes else None,
        license=license.strip() if license and license.strip() else None,
        created_at=now,
        updated_at=now,
    )
    session.add(problem)
    await session.flush()
    await _set_categories(session, problem, category_ids)
    return problem


async def update_problem(
    session: AsyncSession,
    problem: ArenaProblem,
    *,
    title: str,
    source: str | None,
    hide_author_show_source: bool,
    time_limit_ms: int,
    memory_limit_kb: int,
    pids_limit: int,
    output_limit_in_bytes: int,
    problem_statement: str,
    image_b64: str | None,
    image_mime: str | None,
    image_caption: str | None,
    notes: str | None,
    clear_image: bool,
    category_ids: list[str],
    license: str | None = None,
    author: str | None = None,
    author_is_owner: bool = True,
) -> ArenaProblem:
    """Update mutable fields of an existing Arena problem.

    Image bytes must already be processed and base64-encoded by
    ``ImageProcessingService.process_upload_image`` before being passed here.

    Args:
        session: Active async database session.
        problem: The ``ArenaProblem`` to update.
        image_b64: Base64-encoded image string from ``ImageProcessingResult.imagem_base64``,
            or ``None`` if no new image was uploaded.
        image_mime: MIME type from ``ImageProcessingResult.mime_type``, or ``None``.
        image_caption: Optional caption text to display below the image, or ``None``.
        clear_image: When True, removes the existing image even if no new one provided.
        All other args correspond to form fields.

    Returns:
        ArenaProblem: The updated instance (pending flush).

    Raises:
        ValueError: On any validation failure.
    """
    _validate_problem_data(
        title,
        source,
        author,
        author_is_owner,
        license,
        time_limit_ms,
        memory_limit_kb,
        pids_limit,
        output_limit_in_bytes,
        problem_statement,
    )
    problem.title = title.strip()
    problem.author = None if author_is_owner else author.strip() if author else None
    problem.author_is_owner = author_is_owner
    problem.source = source.strip() if source else None
    problem.hide_author_show_source = hide_author_show_source
    problem.time_limit_ms = time_limit_ms
    problem.memory_limit_kb = memory_limit_kb
    problem.pids_limit = pids_limit
    problem.output_limit_in_bytes = output_limit_in_bytes
    problem.problem_statement = problem_statement
    problem.updated_at = _now()

    if image_b64:
        problem.problem_image_base64 = image_b64
        problem.problem_image_mime = image_mime
    elif clear_image:
        problem.problem_image_base64 = None
        problem.problem_image_mime = None

    problem.problem_image_caption = image_caption.strip() if image_caption else None
    problem.notes = notes.strip() if notes else None
    problem.license = license.strip() if license and license.strip() else None

    await _set_categories(session, problem, category_ids)
    return problem


async def toggle_enabled(session: AsyncSession, problem: ArenaProblem) -> ArenaProblem:
    """Toggle the ``enabled`` flag on a problem.

    Args:
        session: Active async database session.
        problem: The problem to enable or disable.

    Returns:
        ArenaProblem: The updated instance (pending flush).
    """
    problem.enabled = not problem.enabled
    problem.updated_at = _now()
    return problem


async def list_owners(session: AsyncSession) -> list[ArenaUser]:
    """Return all users who can own problems, ordered by display name.

    Args:
        session: Active async database session.

    Returns:
        list[ArenaUser]: ARENA_ADMIN users and users with can_edit, sorted by nome.
    """
    result = await session.execute(
        select(ArenaUser)
        .where(or_(ArenaUser.role == ArenaRole.ARENA_ADMIN, ArenaUser.can_edit.is_(True)))
        .order_by(func.lower(ArenaUser.nome))
    )
    return list(result.scalars())


async def search_categories(
    session: AsyncSession,
    *,
    query: str,
    limit: int = 15,
) -> list[ArenaCategory]:
    """Search categories by name for the autocomplete picker.

    Args:
        session: Active async database session.
        query: Substring to search for (case-insensitive).
        limit: Maximum number of results.

    Returns:
        list[ArenaCategory]: Matching categories ordered by name.
    """
    stmt = select(ArenaCategory).order_by(func.lower(ArenaCategory.name))
    if query.strip():
        stmt = stmt.where(ArenaCategory.name.ilike(f"%{query.strip()}%"))
    stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars())


async def delete_problem(session: AsyncSession, problem: ArenaProblem) -> int:
    """Delete a problem and all its dependent data, returning the arena number.

    Deletion order is required because ``arena_submissions.problem_id`` uses
    ``ondelete=RESTRICT``.  Deleting submissions first cascades to judgments,
    test results, AI reviews, and batch jobs; the subsequent problem delete
    cascades to test cases, category map, ratings, solvers, tried, favorites,
    rating history, statistics, and problem-set snapshots.

    Args:
        session: Active async database session (caller commits).
        problem: The ``ArenaProblem`` instance to delete.

    Returns:
        int: The ``arena_number`` of the deleted problem (for flash messages).
    """
    number = problem.arena_number
    await session.execute(_arena_submissions.delete().where(_arena_submissions.c.problem_id == problem.id))
    await session.delete(problem)
    return number


async def build_rejudge_jobs(session: AsyncSession, problem_id: str) -> list[ArenaSubmissionJob]:
    """Create new QUEUED judgment rows for every submission and return their jobs.

    The caller owns the database transaction and must commit before enqueueing
    the returned jobs, so the worker never picks up a job whose rows are not yet
    visible in the database.

    Args:
        session: Active async database session (caller commits).
        problem_id: UUID of the problem whose submissions should be re-judged.

    Returns:
        list[ArenaSubmissionJob]: One ready-to-enqueue job per submission.
    """
    from arena.models.arena_submissions import ArenaSubmissionJudgment

    rows = list(
        (
            await session.execute(
                select(
                    _arena_submissions.c.id,
                    _arena_submissions.c.user_id,
                    _arena_submissions.c.language_id,
                ).where(_arena_submissions.c.problem_id == problem_id)
            )
        ).all()
    )

    jobs: list[ArenaSubmissionJob] = []
    for submission_id, user_id, language_id in rows:
        judgment = ArenaSubmissionJudgment(
            id=str(uuid.uuid4()),
            submission_id=submission_id,
            status=JudgmentStatus.QUEUED.value,
        )
        session.add(judgment)
        await session.flush()
        jobs.append(
            ArenaSubmissionJob(
                judgment_id=judgment.id,
                submission_id=submission_id,
                user_id=user_id,
                problem_id=problem_id,
                language_id=language_id,
                requeue_count=0,
            )
        )
    return jobs
