#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin service for problem CRUD, search, filtering, and pagination.

Access-control pattern:
  - ``is_admin=True``: no owner restriction; caller sees all problems.
  - ``is_admin=False``: list and detail queries are scoped to ``caller_id`` only.

Suggestion autocomplete is intentionally broader for problem editors: it includes all
enabled problems and the caller's own disabled drafts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from arena.models.arena_problems import ArenaCategory, ArenaProblem, ArenaRatingProblem
from arena.models.arena_users import ArenaUser
from arena.services.pagination_service import Pagination, PaginationParams
from arena.services.problem_list_query_service import (
    ProblemListCategory,
    categories_by_problem_id,
    test_case_counts_by_problem_id,
)
from arena.services.problem_search_service import (
    ProblemSuggestionField,
    prepare_problem_search,
    prepare_problem_suggestion_search,
)
from arena.services.text_search_primitives import query_is_trigram_searchable
from shared.db_schema.arena import arena_problem_category_map as _cat_map_table
from shared.db_schema.arena import arena_problem_custom_validators as _custom_validator_table
from shared.db_schema.arena import arena_submissions as _arena_submissions
from shared.db_schema.arena import arena_users as _users_table
from shared.enumerations import (
    ArenaEditorialReleasePolicy,
    ArenaRole,
    CustomValidatorActiveState,
    ProblemValidatorType,
    StatementLanguage,
)
from shared.problem_statement_markdown import validate_md_content
from shared.services.arena_difficulty_display import DifficultyDisplay, difficulty_display
from shared.services.problem_package import (
    DEFAULT_MEMORY_LIMIT_KB,
    DEFAULT_OUTPUT_LIMIT_BYTES,
    DEFAULT_PIDS_LIMIT,
    DEFAULT_TIME_LIMIT_MS,
    MAX_AUTHOR_CHARS,
    MAX_LICENSE_CHARS,
    MAX_SOURCE_CHARS,
    MAX_TITLE_CHARS,
)

# Defaults and field widths are the package format's, so a value that survives a
# form also survives an export/import round trip through either domain.
_DEFAULT_TIME_LIMIT_MS = DEFAULT_TIME_LIMIT_MS
_DEFAULT_MEMORY_LIMIT_KB = DEFAULT_MEMORY_LIMIT_KB
_DEFAULT_PIDS_LIMIT = DEFAULT_PIDS_LIMIT
_DEFAULT_OUTPUT_LIMIT_BYTES = DEFAULT_OUTPUT_LIMIT_BYTES
_MAX_TITLE_LEN = MAX_TITLE_CHARS
_MAX_SOURCE_LEN = MAX_SOURCE_CHARS
_MAX_AUTHOR_LEN = MAX_AUTHOR_CHARS
_MAX_LICENSE_LEN = MAX_LICENSE_CHARS
_MAX_PROBLEM_SUGGESTIONS = 15

# Single source of truth for the admin problem-list sort contract, mirroring
# ``admin_category_service``. Both the list route and the list-URL builder import
# these, so the value omitted from generated URLs cannot drift from the default
# the route re-derives when no sort is supplied.
DEFAULT_SORT = "number_asc"
RELEVANCE_SORT = "relevance"
VALID_SORTS = frozenset(
    {
        RELEVANCE_SORT,
        "title_asc",
        "title_desc",
        "number_asc",
        "number_desc",
        "rating_asc",
        "rating_desc",
    }
)


@dataclass(frozen=True)
class ProblemListItem:
    """Single row in the admin problem list page."""

    id: str
    arena_number: int
    title: str
    enabled: bool
    public_tc_count: int
    private_tc_count: int
    difficulty: DifficultyDisplay
    categories: list[ProblemListCategory]
    has_custom_validator: bool
    has_editorial: bool
    editorial_release_policy: ArenaEditorialReleasePolicy


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
    editorial: str | None,
    expected_difficulty: int | None = None,
) -> None:
    """Validate problem form data and raise ValueError on any violation.

    Image validation is handled upstream by ``ImageProcessingService`` before
    the service is called; only scalar fields are validated here.
    """
    if expected_difficulty is not None and not 1 <= expected_difficulty <= 100:
        raise ValueError("Expected difficulty must be between 1 and 100.")
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
    if time_limit_ms < 1:
        raise ValueError("Time limit must be at least 1 ms.")
    if memory_limit_kb < 1:
        raise ValueError("Memory limit must be at least 1 KB.")
    if pids_limit < 1:
        raise ValueError("PIDs limit must be at least 1.")
    if output_limit_in_bytes < 1:
        raise ValueError("Output limit must be at least 1 byte.")
    if not problem_statement.strip():
        raise ValueError("Markdown statement cannot be empty.")
    md_errors = validate_md_content(problem_statement)
    if md_errors:
        raise ValueError(md_errors[0])
    if editorial and editorial.strip():
        editorial_errors = validate_md_content(editorial)
        if editorial_errors:
            raise ValueError(f"Editorial: {editorial_errors[0]}")


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


def _apply_sort(stmt: Select[Any], sort_by: str, relevance: Any | None = None) -> Select[Any]:
    """Append ORDER BY clause for the given sort key."""
    if sort_by == RELEVANCE_SORT and relevance is not None:
        return stmt.order_by(
            relevance.c.exact_number_match.desc(),
            relevance.c.full_text_match.desc(),
            relevance.c.full_text_rank.desc(),
            relevance.c.trigram_rank.desc(),
            ArenaProblem.arena_number.asc(),
        )
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
    language: StatementLanguage | None = None,
    enabled: bool | None = None,
    editorial: str | None = None,
    sort_by: str = "",
    caller_id: str,
    is_admin: bool,
) -> Pagination[ProblemListItem]:
    """Return a paginated list of problems with search and filter support.

    Args:
        session: Active async database session.
        page: 1-based page number.
        per_page: Number of items per page.
        search: Free-text search applied to arena number, title, statement, source, and author.
        category_ids: Require ANY listed category ID (OR semantics). None = no filter.
        category_slugs: Require ANY listed category slug (OR semantics). None = no filter.
        owner_id: Restrict to a specific owner (admin-only filter). None = no filter.
        language: Restrict to problems whose statement is in this language. None = no filter.
        enabled: Restrict to enabled (True) or disabled (False) problems. None = no filter.
        editorial: Restrict by editorial state -- ``"none"`` for problems with no editorial
            text, or one of ``"never"``/``"always"``/``"after_ac"`` for problems that have
            editorial text under that release policy. None = no filter.
        sort_by: One of the ``VALID_SORTS`` values.
        caller_id: UUID of the requesting user.
        is_admin: When False, scopes the query to problems owned by ``caller_id``.

    Returns:
        Pagination[ProblemListItem]: Paginated result with problem rows.
    """
    normalized_search = search.strip()
    default_sort = RELEVANCE_SORT if normalized_search else DEFAULT_SORT
    effective_sort = sort_by if sort_by in VALID_SORTS else default_sort
    if effective_sort == RELEVANCE_SORT and not normalized_search:
        effective_sort = DEFAULT_SORT
    params = PaginationParams(page=max(1, page), per_page=max(1, per_page))

    # Keep this statement filter-only. The count must not evaluate display
    # projections or page enrichments for every matching problem.
    filtered_problem_ids = select(ArenaProblem.id)

    if not is_admin:
        filtered_problem_ids = filtered_problem_ids.where(ArenaProblem.owner_id == caller_id)
    elif owner_id:
        filtered_problem_ids = filtered_problem_ids.where(ArenaProblem.owner_id == owner_id)

    if language is not None:
        filtered_problem_ids = filtered_problem_ids.where(ArenaProblem.statement_language == language)

    if enabled is not None:
        filtered_problem_ids = filtered_problem_ids.where(ArenaProblem.enabled == enabled)

    if editorial == "none":
        filtered_problem_ids = filtered_problem_ids.where(ArenaProblem.editorial.is_(None))
    elif editorial in ("never", "always", "after_ac"):
        filtered_problem_ids = filtered_problem_ids.where(
            ArenaProblem.editorial.is_not(None),
            ArenaProblem.editorial_release_policy == ArenaEditorialReleasePolicy(editorial),
        )

    if normalized_search:
        search_expressions = await prepare_problem_search(session, normalized_search)
        filtered_problem_ids = (
            filtered_problem_ids.outerjoin(
                _users_table,
                ArenaProblem.owner_id == _users_table.c.id,
            )
            .add_columns(
                search_expressions.exact_number_match.label("exact_number_match"),
                search_expressions.full_text_match.label("full_text_match"),
                search_expressions.full_text_rank.label("full_text_rank"),
                search_expressions.trigram_rank.label("trigram_rank"),
            )
            .where(search_expressions.predicate)
        )

    if category_slugs:
        effective_slugs = list(dict.fromkeys(slug.strip().lower() for slug in category_slugs if slug.strip()))
        if effective_slugs:
            # OR semantics: one matching category slug is enough to include the problem.
            matching_category = (
                select(_cat_map_table.c.category_id)
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
            )
            filtered_problem_ids = filtered_problem_ids.where(matching_category.exists())
    elif category_ids:
        effective_ids = list(dict.fromkeys(category_ids))
        # OR semantics: one matching category is enough to include the problem.
        matching_category = select(_cat_map_table.c.category_id).where(
            _cat_map_table.c.problem_id == ArenaProblem.id,
            _cat_map_table.c.category_id.in_(effective_ids),
        )
        filtered_problem_ids = filtered_problem_ids.where(matching_category.exists())

    count_stmt = select(func.count()).select_from(filtered_problem_ids.subquery())
    total: int = (await session.execute(count_stmt)).scalar_one()

    filtered_ids = filtered_problem_ids.subquery()
    display_statement = (
        select(
            ArenaProblem.id,
            ArenaProblem.arena_number,
            ArenaProblem.title,
            ArenaProblem.enabled,
            ArenaProblem.validator_type,
            ArenaProblem.editorial,
            ArenaProblem.editorial_release_policy,
            ArenaRatingProblem.rating.label("rating_value"),
            ArenaRatingProblem.attempted_users,
            ArenaProblem.expected_difficulty,
        )
        .join(filtered_ids, filtered_ids.c.id == ArenaProblem.id)
        .outerjoin(ArenaRatingProblem, ArenaProblem.id == ArenaRatingProblem.problem_id)
    )
    paginated = (
        _apply_sort(display_statement, effective_sort, filtered_ids if normalized_search else None)
        .offset(params.offset)
        .limit(params.per_page)
    )
    rows = list((await session.execute(paginated)).all())
    problem_ids = [row.id for row in rows]

    categories = await categories_by_problem_id(session, problem_ids)
    test_case_counts = await test_case_counts_by_problem_id(session, problem_ids)

    items: list[ProblemListItem] = []
    for row in rows:
        public_tc_count, private_tc_count = test_case_counts.get(row.id, (0, 0))
        items.append(
            ProblemListItem(
                id=row.id,
                arena_number=row.arena_number,
                title=row.title,
                enabled=row.enabled,
                public_tc_count=public_tc_count,
                private_tc_count=private_tc_count,
                difficulty=difficulty_display(row.rating_value, row.attempted_users, row.expected_difficulty),
                categories=categories.get(row.id, []),
                has_custom_validator=row.validator_type is ProblemValidatorType.INTERACTIVE,
                has_editorial=row.editorial is not None,
                editorial_release_policy=row.editorial_release_policy,
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
            selectinload(ArenaProblem.custom_validator),
            # The export builder runs in a worker thread, so every collection it
            # touches must already be loaded — a lazy load there has no event loop.
            selectinload(ArenaProblem.sample_interactions),
        )
    )
    if not is_admin:
        stmt = stmt.where(ArenaProblem.owner_id == caller_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_problem_definition(
    session: AsyncSession,
    problem_id: str,
    *,
    caller_id: str,
    is_admin: bool,
) -> ArenaProblem | None:
    """Fetch a definition-editor problem without judgment relationships.

    Args:
        session: Active database session.
        problem_id: UUID of the problem.
        caller_id: UUID of the requesting user.
        is_admin: When False, scope the problem to ``caller_id``.

    Returns:
        The problem with categories loaded, or ``None`` when unavailable.
    """
    stmt = select(ArenaProblem).where(ArenaProblem.id == problem_id).options(selectinload(ArenaProblem.categories))
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
    validator_type: ProblemValidatorType,
    license: str | None = None,
    author: str | None = None,
    author_is_owner: bool = True,
    statement_language: StatementLanguage | None = None,
    editorial: str | None = None,
    editorial_release_policy: ArenaEditorialReleasePolicy = ArenaEditorialReleasePolicy.NEVER,
    expected_difficulty: int | None = None,
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
        validator_type: The problem's validation strategy, chosen here and
            immutable afterwards.
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
        editorial,
        expected_difficulty,
    )

    now = _now()
    problem = ArenaProblem(
        id=str(uuid.uuid4()),
        title=title.strip(),
        validator_type=validator_type,
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
        editorial=editorial if editorial and editorial.strip() else None,
        editorial_release_policy=editorial_release_policy,
        problem_image_base64=image_b64,
        problem_image_mime=image_mime if image_b64 else None,
        problem_image_caption=image_caption.strip() if image_caption else None,
        notes=notes.strip() if notes else None,
        license=license.strip() if license and license.strip() else None,
        statement_language=statement_language,
        expected_difficulty=expected_difficulty,
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
    statement_language: StatementLanguage | None = None,
    validator_type: ProblemValidatorType | None = None,
    editorial: str | None = None,
    editorial_release_policy: ArenaEditorialReleasePolicy = ArenaEditorialReleasePolicy.NEVER,
    expected_difficulty: int | None = None,
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
        validator_type: Rejected unless it equals the stored strategy. Present so
            a caller that echoes the value back cannot silently change it.
        All other args correspond to form fields.

    Returns:
        ArenaProblem: The updated instance (pending flush).

    Raises:
        ValueError: On any validation failure, including a disagreeing
            ``validator_type``.
    """
    if validator_type is not None and validator_type is not problem.validator_type:
        message = "A problem's validation strategy is immutable and cannot be changed on edit."
        raise ValueError(message)
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
        editorial,
        expected_difficulty,
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
    problem.editorial = editorial if editorial and editorial.strip() else None
    problem.editorial_release_policy = editorial_release_policy
    problem.updated_at = _now()

    # A new upload wins over the remove checkbox. Removing the image removes its
    # caption too: a caption with no image to caption is meaningless.
    if image_b64:
        problem.problem_image_base64 = image_b64
        problem.problem_image_mime = image_mime
        problem.problem_image_caption = image_caption.strip() if image_caption else None
    elif clear_image:
        problem.problem_image_base64 = None
        problem.problem_image_mime = None
        problem.problem_image_caption = None
    else:
        problem.problem_image_caption = image_caption.strip() if image_caption else None
    problem.notes = notes.strip() if notes else None
    problem.license = license.strip() if license and license.strip() else None
    problem.statement_language = statement_language
    problem.expected_difficulty = expected_difficulty

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
    if not problem.enabled:
        validator = (
            await session.execute(
                select(
                    _custom_validator_table.c.active_state,
                    _custom_validator_table.c.active_source,
                    _custom_validator_table.c.candidate_source,
                ).where(_custom_validator_table.c.problem_id == problem.id)
            )
        ).one_or_none()
        if (
            validator is not None
            and (validator.active_source is not None or validator.candidate_source is not None)
            and validator.active_state != CustomValidatorActiveState.VALID
        ):
            raise ValueError("Compile a valid custom validator before enabling this problem.")
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


async def search_problem_suggestions(
    session: AsyncSession,
    *,
    field: ProblemSuggestionField,
    query: str,
    caller_id: str,
    is_admin: bool,
) -> list[str]:
    """Return visible, distinct problem-metadata values matching an autocomplete query.

    Args:
        session: Active async database session.
        field: Stored free-text field to project: ``"author"``, ``"license"``, or
            ``"source"``.
        query: Literal text to search after surrounding whitespace is removed.
            Each whitespace-separated term is matched independently as a
            substring, so terms may be partial, out of order, or begin
            mid-word.
        caller_id: UUID of the requesting user.
        is_admin: When False, includes enabled problems plus drafts owned by ``caller_id``.

    Returns:
        Matching, trimmed values in deterministic relevance/name order. Owner-backed
        authors, nulls, and blank legacy values are excluded.
    """
    normalized_query = query.strip()
    # Every term must carry a trigram PostgreSQL can index-match. A shorter term
    # is not merely unhelpful: no branch can answer it without reading every row
    # (full-text matching compares whole lexemes, and both `ILIKE '%xx%'` and the
    # `%` similarity operator fall back to a sequential scan), so the search is
    # declined rather than served expensively.
    if not query_is_trigram_searchable(normalized_query):
        return []

    search_expressions = await prepare_problem_suggestion_search(session, field, normalized_query)
    stored_value = {
        "author": ArenaProblem.author,
        "license": ArenaProblem.license,
        "source": ArenaProblem.source,
    }[field]
    normalized_value = func.trim(stored_value).label("suggestion_value")
    full_text_rank = func.max(search_expressions.full_text_rank).label("full_text_rank")
    trigram_rank = func.max(search_expressions.trigram_rank).label("trigram_rank")

    stmt = (
        select(normalized_value)
        .where(
            search_expressions.predicate,
            stored_value.is_not(None),
            func.length(normalized_value) > 0,
        )
        .group_by(normalized_value)
        .order_by(
            full_text_rank.desc(),
            trigram_rank.desc(),
            func.lower(normalized_value).asc(),
            normalized_value.asc(),
        )
        .limit(_MAX_PROBLEM_SUGGESTIONS)
    )
    if not is_admin:
        stmt = stmt.where(
            or_(
                ArenaProblem.enabled.is_(True),
                ArenaProblem.owner_id == caller_id,
            )
        )

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
