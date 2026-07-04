#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Problem browsing routes for the Arena platform.

Only ``GET /problems`` (the list) is public; it keeps the optional
``current_user`` dependency, following the same pattern used in
``arena/routes/root.py`` and ``arena/routes/help.py``.  Every other route here
(problem detail and its sub-resources, submit, favorite, request-removal)
requires a logged-in user via ``require_arena_user``.

Routes:
  GET  /problems                                        arena_problem_list
  GET  /problems/{arena_number}                         arena_problem_detail
  GET  /problems/{arena_number}/rating-history          arena_problem_rating_history_public
  GET  /problems/{arena_number}/statistics              arena_problem_statistics
  GET  /problems/{arena_number}/statistics.json         arena_problem_statistics_data
  GET  /problems/{arena_number}/sample-testcases.zip    arena_problem_sample_testcases_zip
  POST /problems/{arena_number}/submit                  arena_problem_submit
  POST /problems/{arena_number}/favorite                arena_problem_toggle_favorite
  POST /problems/{arena_number}/request-removal         arena_problem_request_removal
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlencode

import anyio
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user, require_arena_user
from arena.models.arena_users import ArenaUser
from arena.services import (
    arena_favorite_service,
    problem_browse_service,
    problem_stats_service,
    problem_tc_export_service,
)
from arena.services.arena_problem_set_service import (
    AcceptingSetInfo,
    problem_accepting_set_for_user,
)
from arena.services.pagination_service import parse_page
from arena.services.submission_service import (
    ArenaSubmissionRateLimitError,
    ArenaSubmissionServiceError,
    create_arena_submission,
)
from arena.services.user_timezone_service import format_user_datetime
from shared.db_schema import languages as languages_table
from shared.db_schema.arena import arena_submissions
from shared.enumerations import ArenaNotificationKind, ArenaRole
from shared.language_registry import ace_mode_for_language_id, default_stub_for_language_id
from shared.services.arena_notification_service import create_arena_notification
from shared.services.testcase_files import read_testcase_full
from shared.services.valkey_service.queue_ops import enqueue_arena_submission_job

logger = logging.getLogger(__name__)

router = APIRouter(tags=["arena-problems"])

_DEFAULT_SORT = "number_asc"
_VALID_SORTS = {
    "number_asc",
    "number_desc",
    "title_asc",
    "title_desc",
    "rating_asc",
    "rating_desc",
    "solvers_asc",
    "solvers_desc",
}


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def _safe_sort(sort_by: str | None) -> str:
    """Return a validated sort key, falling back to the default."""
    return sort_by if sort_by in _VALID_SORTS else _DEFAULT_SORT


def _relative_time(dt: datetime) -> str:
    """Return a human-readable relative time string for *dt* vs. now (UTC).

    Args:
        dt: An aware or naïve datetime to compare against UTC now.

    Returns:
        str: A phrase like "3 days ago" or "just now".
    """
    now = datetime.now(UTC)
    aware_dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
    delta = now - aware_dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        m = seconds // 60
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if seconds < 86400:
        h = seconds // 3600
        return f"{h} hour{'s' if h != 1 else ''} ago"
    if seconds < 7 * 86400:
        d = seconds // 86400
        return f"{d} day{'s' if d != 1 else ''} ago"
    if seconds < 30 * 86400:
        w = seconds // (7 * 86400)
        return f"{w} week{'s' if w != 1 else ''} ago"
    if seconds < 365 * 86400:
        mo = seconds // (30 * 86400)
        return f"{mo} month{'s' if mo != 1 else ''} ago"
    y = seconds // (365 * 86400)
    return f"{y} year{'s' if y != 1 else ''} ago"


def _problem_detail_url(
    request: Request,
    *,
    arena_number: int,
    back_page: str,
    back_search: str,
    back_sort_by: str,
    back_category_slugs: list[str],
) -> str:
    """Build a problem detail URL preserving back-state query params.

    Args:
        request: Current HTTP request.
        arena_number: Target problem's public arena number.
        back_page: Page number to restore when returning to the list.
        back_search: Search query to restore.
        back_sort_by: Sort key to restore.
        back_category_slugs: Category filter slugs to restore.

    Returns:
        str: URL for the problem detail page with back-state params attached.
    """
    params: dict[str, str] = {}
    if back_page and back_page != "1":
        params["back_page"] = back_page
    if back_search:
        params["back_search"] = back_search
    if back_sort_by and back_sort_by != _DEFAULT_SORT:
        params["back_sort_by"] = back_sort_by
    base_url = str(request.url_for("arena_problem_detail", arena_number=arena_number))
    qs = urlencode(params)
    cat_qs = urlencode({"back_category_slugs": back_category_slugs}, doseq=True)
    query_parts = [p for p in (qs, cat_qs) if p]
    return f"{base_url}?{'&'.join(query_parts)}" if query_parts else base_url


def _problem_list_back_url(
    request: Request,
    *,
    back_page: str,
    back_search: str,
    back_sort_by: str,
    back_category_slugs: list[str],
) -> str:
    """Reconstruct the problem list URL from the back-state query params.

    Args:
        request: Current HTTP request (used to resolve the list route URL).
        back_page: Page number to return to.
        back_search: Search query to restore.
        back_sort_by: Sort key to restore.
        back_category_slugs: Category filter slugs to restore.

    Returns:
        str: Fully-qualified URL for the problem list with state restored.
    """
    params: dict[str, str] = {}
    if back_page and back_page != "1":
        params["page"] = back_page
    if back_search:
        params["search"] = back_search
    if back_sort_by and back_sort_by != _DEFAULT_SORT:
        params["sort_by"] = back_sort_by
    base_url = str(request.url_for("arena_problem_list"))
    qs = urlencode(params)
    cat_qs = urlencode({"category_slugs": back_category_slugs}, doseq=True)
    query_parts = [p for p in (qs, cat_qs) if p]
    return f"{base_url}?{'&'.join(query_parts)}" if query_parts else base_url


@router.get("/problems", response_class=HTMLResponse, name="arena_problem_list")
async def arena_problem_list(
    request: Request,
    search: str = "",
    sort_by: str = _DEFAULT_SORT,
    category_slugs: list[str] | None = Query(None),
    page: int = 1,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the public problem list page.

    Shows all enabled Arena problems in a sortable, searchable, paginated table
    (25 per page).  This is the one Arena content page that remains public; it
    renders for both logged-in users and guests.

    Args:
        request: The current HTTP request.
        search: Free-text search over number, title, source, and author name.
        sort_by: Column sort key (number_asc default).
        category_slugs: Category slugs for AND-based filtering.
        page: 1-based page number.
        current_user: Authenticated ``ArenaUser`` or ``None`` for guests.
        session: Async database session.
    """
    effective_sort = _safe_sort(sort_by)
    effective_page = parse_page(page)

    pagination = await problem_browse_service.list_enabled_problems_paginated(
        session,
        page=effective_page,
        per_page=25,
        search=search,
        category_slugs=category_slugs or None,
        sort_by=effective_sort,
        user_id=current_user.id if current_user else None,
    )
    all_categories = await problem_browse_service.get_all_categories(session)

    # Build the query-string fragment that detail links carry back to the list,
    # so the "Back to list" button can reconstruct the exact filtered/sorted state.
    back_params: dict[str, str] = {"back_page": str(effective_page), "back_sort_by": effective_sort}
    if search:
        back_params["back_search"] = search
    back_params_qs = urlencode(back_params)
    if category_slugs:
        back_params_qs += "&" + urlencode({"back_category_slugs": category_slugs}, doseq=True)

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "problems/problem_list.html",
            {
                "current_user": current_user,
                "pagination": pagination,
                "search": search,
                "sort_by": effective_sort,
                "selected_category_slugs": set(category_slugs or []),
                "all_categories": all_categories,
                "page": effective_page,
                "back_params_qs": back_params_qs,
            },
        )
    )


@router.get("/problems/{arena_number:int}", response_class=HTMLResponse, name="arena_problem_detail")
async def arena_problem_detail(
    request: Request,
    arena_number: int,
    back_page: str = "1",
    back_search: str = "",
    back_sort_by: str = _DEFAULT_SORT,
    back_category_slugs: list[str] = Query(default=[]),
    continue_submission: str | None = Query(default=None, alias="continue"),
    current_user: ArenaUser = Depends(require_arena_user),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the problem detail page.

    Shows the problem statement, sample test cases, resource limits, and a
    submission stub panel.  Displays the current user's solve/attempt status
    if logged in.

    When the optional ``continue`` query parameter is provided, the route looks
    up that submission.  If it belongs to the current user and targets the same
    problem, its source code and language are pre-filled in the editor.

    Args:
        request: The current HTTP request.
        arena_number: Public arena number of the problem.
        back_page: Page number to restore when returning to the list.
        back_search: Search query to restore.
        back_sort_by: Sort key to restore.
        back_category_slugs: Category filter slugs to restore.
        continue_submission: Optional submission UUID whose source code should
            pre-fill the editor.  Silently ignored if the submission does not
            belong to the current user or targets a different problem.
        current_user: The authenticated Arena user (login required).
        session: Async database session.
    """
    result = await problem_browse_service.get_enabled_problem_by_number(session, arena_number)
    if result is None:
        raise HTTPException(status_code=404, detail="Problem not found")
    problem, author_info = result

    raw_sample_tcs = sorted([tc for tc in problem.test_cases if tc.is_sample], key=lambda tc: tc.ordinal)
    # PROBLEM_TESTCASE_DIR already resolves to the arena namespace (<root>/arena);
    # do not append ARENA_TC_SUBDIR again or the path becomes <root>/arena/arena.
    tc_dir = settings.PROBLEM_TESTCASE_DIR
    sample_test_cases = []
    for tc in raw_sample_tcs:

        def _read(ordinal: int = tc.ordinal) -> tuple[str, str]:
            return read_testcase_full(problem.id, ordinal, tc_dir)

        in_content, out_content = await anyio.to_thread.run_sync(_read)
        sample_test_cases.append(
            {
                "input_content": in_content,
                "output_content": out_content,
                "explanation": tc.explanation,
                "ordinal": tc.ordinal,
            }
        )

    lang_result = await session.execute(
        select(languages_table)
        .where(languages_table.c.active == True)  # noqa: E712
        .order_by(languages_table.c.name)
    )
    active_languages = lang_result.mappings().all()
    ace_modes = {row["id"]: ace_mode_for_language_id(row["id"]) for row in active_languages}
    language_stubs = {row["id"]: default_stub_for_language_id(row["id"]) for row in active_languages}

    solved_at: datetime | None = None
    tried_at: datetime | None = None
    relative_solved: str | None = None
    is_favorite: bool = False
    accepting_set: AcceptingSetInfo | None = None
    solved_at, tried_at, is_favorite = await problem_browse_service.get_user_problem_status(
        session, user_id=current_user.id, problem_id=problem.id
    )
    if solved_at is not None:
        relative_solved = _relative_time(solved_at)
    accepting_set = await problem_accepting_set_for_user(
        session,
        problem_id=problem.id,
        user_id=current_user.id,
        now=datetime.now(UTC),
    )

    # Resolve the optional ?continue=<submission_id> prefill
    prefill_source_code: str | None = None
    prefill_language_id: str | None = None
    if continue_submission:
        sub_row = (
            await session.execute(
                select(
                    arena_submissions.c.source_code,
                    arena_submissions.c.language_id,
                )
                .where(arena_submissions.c.id == continue_submission)
                .where(arena_submissions.c.user_id == current_user.id)
                .where(arena_submissions.c.problem_id == problem.id)
            )
        ).one_or_none()
        if sub_row is not None:
            prefill_source_code = sub_row[0]
            prefill_language_id = sub_row[1]

    back_url = _problem_list_back_url(
        request,
        back_page=back_page,
        back_search=back_search,
        back_sort_by=back_sort_by,
        back_category_slugs=back_category_slugs,
    )
    rating_history_url = str(request.url_for("arena_problem_rating_history_public", arena_number=arena_number))

    prev_number, next_number = await problem_browse_service.get_adjacent_problem_numbers(session, arena_number)
    prev_problem_url: str | None = None
    next_problem_url: str | None = None
    if prev_number is not None:
        prev_problem_url = _problem_detail_url(
            request,
            arena_number=prev_number,
            back_page=back_page,
            back_search=back_search,
            back_sort_by=back_sort_by,
            back_category_slugs=back_category_slugs,
        )
    if next_number is not None:
        next_problem_url = _problem_detail_url(
            request,
            arena_number=next_number,
            back_page=back_page,
            back_search=back_search,
            back_sort_by=back_sort_by,
            back_category_slugs=back_category_slugs,
        )

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "problems/problem_detail.html",
            {
                "current_user": current_user,
                "problem": problem,
                "author_info": author_info,
                "sample_test_cases": sample_test_cases,
                "active_languages": active_languages,
                "ace_modes": ace_modes,
                "language_stubs": language_stubs,
                "solved_at": solved_at,
                "tried_at": tried_at,
                "relative_solved": relative_solved,
                "is_favorite": is_favorite,
                "back_url": back_url,
                "rating_history_url": rating_history_url,
                "prefill_source_code": prefill_source_code,
                "prefill_language_id": prefill_language_id,
                "accepting_set": accepting_set,
                "prev_problem_url": prev_problem_url,
                "next_problem_url": next_problem_url,
                "prev_problem_number": prev_number,
                "next_problem_number": next_number,
            },
        )
    )


@router.get(
    "/problems/{arena_number:int}/rating-history",
    name="arena_problem_rating_history_public",
)
async def arena_problem_rating_history_public(
    arena_number: int,
    current_user: ArenaUser = Depends(require_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return the rating history for a problem.

    Requires a logged-in user.  Only enabled problems are reachable.

    Args:
        arena_number: Public arena number of the problem.
        session: Async database session.

    Returns:
        JSONResponse: ``{"history": [{"ts": "<ISO8601>", "rating": <float>}, ...]}``
    """
    result = await problem_browse_service.get_enabled_problem_by_number(session, arena_number)
    if result is None:
        raise HTTPException(status_code=404, detail="Problem not found")
    problem, _ = result
    history = await problem_browse_service.get_problem_rating_history(session, problem.id)
    for row in history:
        row["ts_display"] = format_user_datetime(
            datetime.fromisoformat(str(row["ts"])),
            current_user,
            "%Y-%m-%d",
        )
    return JSONResponse({"history": history})


@router.get(
    "/problems/{arena_number:int}/statistics",
    response_class=HTMLResponse,
    name="arena_problem_statistics",
)
async def arena_problem_statistics(
    request: Request,
    arena_number: int,
    current_user: ArenaUser = Depends(require_arena_user),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the per-problem statistics page.

    The charts and tables are populated client-side from the precomputed
    statistics JSON endpoint and the existing rating-history endpoint.

    Args:
        request: The current HTTP request.
        arena_number: Public arena number of the problem.
        current_user: The authenticated Arena user (login required).
        session: Async database session.

    Returns:
        HTMLResponse: The rendered statistics page.
    """
    result = await problem_browse_service.get_enabled_problem_by_number(session, arena_number)
    if result is None:
        raise HTTPException(status_code=404, detail="Problem not found")
    problem, _ = result

    rating_history_url = str(request.url_for("arena_problem_rating_history_public", arena_number=arena_number))
    stats_data_url = str(request.url_for("arena_problem_statistics_data", arena_number=arena_number))
    detail_url = str(request.url_for("arena_problem_detail", arena_number=arena_number))

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "problems/problem_statistics.html",
            {
                "current_user": current_user,
                "problem": problem,
                "rating_history_url": rating_history_url,
                "stats_data_url": stats_data_url,
                "detail_url": detail_url,
            },
        )
    )


@router.get(
    "/problems/{arena_number:int}/statistics.json",
    name="arena_problem_statistics_data",
)
async def arena_problem_statistics_data(
    arena_number: int,
    current_user: ArenaUser = Depends(require_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return the precomputed statistics payload for a problem.

    Requires a logged-in user.  Only enabled problems are reachable.  Returns
    ``{}`` when statistics have not been computed yet so the client can render
    an empty state.

    Args:
        arena_number: Public arena number of the problem.
        session: Async database session.

    Returns:
        JSONResponse: The precomputed statistics payload, or ``{}``.
    """
    result = await problem_browse_service.get_enabled_problem_by_number(session, arena_number)
    if result is None:
        raise HTTPException(status_code=404, detail="Problem not found")
    problem, _ = result
    stats = await problem_stats_service.get_problem_statistics(session, problem.id)
    if stats and stats.get("computed_at"):
        stats["computed_at_display"] = format_user_datetime(
            datetime.fromisoformat(str(stats["computed_at"])),
            current_user,
        )
    return JSONResponse(stats or {})


@router.get(
    "/problems/{arena_number:int}/sample-testcases.zip",
    name="arena_problem_sample_testcases_zip",
)
async def arena_problem_sample_testcases_zip(
    arena_number: int,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Download a ZIP archive of the sample test cases for a problem.

    Layout A format: ``in/001.in`` + ``out/001.out`` (ordinal-based naming,
    zero-padded to three digits).  Requires a logged-in user.

    Args:
        arena_number: Public arena number of the problem.
        session: Async database session.

    Returns:
        Response: application/zip attachment, or 404 when the problem is
            disabled/missing or has no sample test cases.
    """
    result = await problem_browse_service.get_enabled_problem_by_number(session, arena_number)
    if result is None:
        raise HTTPException(status_code=404, detail="Problem not found")
    problem, _ = result

    sample_tcs = [tc for tc in problem.test_cases if tc.is_sample]
    if not sample_tcs:
        raise HTTPException(status_code=404, detail="No sample test cases available.")

    zip_bytes = await anyio.to_thread.run_sync(
        lambda: problem_tc_export_service.build_sample_testcases_zip(
            problem.id, sample_tcs, settings.PROBLEM_TESTCASE_DIR
        )
    )
    filename = f"sample-testcases-{arena_number}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/problems/{arena_number:int}/submit", name="arena_problem_submit")
async def arena_problem_submit(
    request: Request,
    arena_number: int,
    flash: FlashDep,
    language_id: str = Form(default=""),
    source_code: str = Form(default=""),
    problem_set_id: str = Form(default=""),
    current_user: ArenaUser = Depends(require_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Accept a code submission for an Arena problem.

    Creates an ``arena_submissions`` row and an ``arena_submission_judgments``
    row in QUEUED state, commits the transaction, then enqueues the job for the
    autojudge worker.  Redirects to the user's submissions tab on success.

    ``language_id`` and ``source_code`` default to empty string so browsers can
    submit an empty textarea without a 422 — the service validates both fields
    and raises ``ArenaSubmissionServiceError`` which is caught and flashed back.

    Args:
        request: Current HTTP request.
        arena_number: Public arena number of the problem.
        flash: Flash message dependency.
        language_id: Selected language ID (from the submission form).
        source_code: Raw source code submitted by the user.
        current_user: The authenticated Arena user (login required).
        session: Active database session.

    Returns:
        Response: 303 redirect to submissions tab on success, or back to the
            problem detail page with a flash error on failure.
    """
    result = await problem_browse_service.get_enabled_problem_by_number(session, arena_number)
    if result is None:
        raise HTTPException(status_code=404, detail="Problem not found")
    problem, _ = result

    detail_url = str(request.url_for("arena_problem_detail", arena_number=arena_number))
    bypass = current_user.role in {ArenaRole.ARENA_ADMIN, ArenaRole.ARENA_JUDGE}
    try:
        creation = await create_arena_submission(
            session=session,
            user_id=current_user.id,
            problem_id=problem.id,
            language_id=language_id,
            source_code=source_code,
            problem_set_id=problem_set_id or None,
            bypass_rate_limit=bypass,
            rate_limit_window_minutes=settings.ARENA_RATE_LIMIT_WINDOW_MINUTES,
            rate_limit_max_submissions=settings.ARENA_RATE_LIMIT_MAX_SUBMISSIONS,
        )
    except ArenaSubmissionRateLimitError as exc:
        next_time = format_user_datetime(exc.next_allowed_at, current_user, "%H:%M:%S %Z")
        flash(f"Submission limit reached. You can submit again after {next_time}.", FlashCategory.DANGER)
        return RedirectResponse(url=detail_url, status_code=303)
    except ArenaSubmissionServiceError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(url=detail_url, status_code=303)

    await session.commit()
    await enqueue_arena_submission_job(request.app.state.valkey_runtime, creation.job)

    if creation.rate_limit_exceeded:
        message = (
            f"Rate limit exceeded "
            f"({settings.ARENA_RATE_LIMIT_MAX_SUBMISSIONS} per "
            f"{settings.ARENA_RATE_LIMIT_WINDOW_MINUTES} min) — allowed as staff."
        )
        if creation.rate_limit_next_allowed_at is not None:
            next_time = format_user_datetime(creation.rate_limit_next_allowed_at, current_user, "%H:%M:%S %Z")
            message += f" A regular user could submit again after {next_time}."
        flash(message, FlashCategory.WARNING)

    profile_url = str(request.url_for("arena_user_profile")) + "?tab=submissions"
    return RedirectResponse(url=profile_url, status_code=303)


@router.post("/problems/{arena_number:int}/favorite", name="arena_problem_toggle_favorite")
async def arena_problem_toggle_favorite(
    request: Request,
    arena_number: int,
    current_user: ArenaUser = Depends(require_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Toggle the favorite status of a problem for the current user.

    Returns JSON ``{"is_favorite": true/false}``.  Unknown or disabled problems
    receive a 404.

    Args:
        request: Current HTTP request.
        arena_number: Public arena number of the problem.
        current_user: The authenticated Arena user (login required).
        session: Active database session.

    Returns:
        JSONResponse: ``{"is_favorite": <bool>}`` on success.
    """
    result = await problem_browse_service.get_enabled_problem_by_number(session, arena_number)
    if result is None:
        raise HTTPException(status_code=404, detail="Problem not found")
    problem, _ = result

    now_favorite = await arena_favorite_service.toggle_favorite(
        session,
        user_id=current_user.id,
        problem_id=problem.id,
    )
    await session.commit()
    return JSONResponse({"is_favorite": now_favorite})


@router.post("/problems/{arena_number:int}/request-removal", name="arena_problem_request_removal")
async def arena_problem_request_removal(
    request: Request,
    arena_number: int,
    flash: FlashDep,
    current_user: ArenaUser = Depends(require_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Handle a problem removal request submitted by the problem's owner.

    Only the problem owner without edit rights may trigger this flow. Sends a
    durable notification to every ARENA_ADMIN user linking them to the admin
    edit page.  Uses ``source_ref`` idempotency so repeated requests do not
    create duplicate notifications.

    Args:
        request: Current HTTP request.
        arena_number: Public arena number of the problem.
        flash: Flash message dependency.
        current_user: The authenticated Arena user (login required).
        session: Active database session.

    Returns:
        Response: 303 redirect to the problem detail page.
    """
    result = await problem_browse_service.get_enabled_problem_by_number(session, arena_number)
    if result is None:
        raise HTTPException(status_code=404, detail="Problem not found")
    problem, _ = result

    detail_url = str(request.url_for("arena_problem_detail", arena_number=arena_number))

    if current_user.id != problem.owner_id:
        raise HTTPException(status_code=403, detail="Not the problem owner")
    if current_user.role == ArenaRole.ARENA_ADMIN or current_user.can_edit:
        raise HTTPException(status_code=403, detail="Use the edit page to manage this problem")

    admin_rows = await session.execute(select(ArenaUser).where(ArenaUser.role == ArenaRole.ARENA_ADMIN))
    admins = admin_rows.scalars().all()

    edit_url = str(request.url_for("arena_admin_problem_edit", problem_id=problem.id))
    source_ref = f"problem-removal-request:{problem.id}:{current_user.id}"
    for admin in admins:
        await create_arena_notification(
            session,
            user_id=admin.id,
            notification_kind=ArenaNotificationKind.PROBLEM_REMOVAL_REQUEST,
            title="Problem removal requested",
            message=(f'{current_user.nome} requested removal of problem "#{problem.arena_number} — {problem.title}".'),
            target_url=edit_url,
            source_ref=source_ref,
        )

    await session.commit()
    flash("Your removal request has been sent to the administrators.", FlashCategory.SUCCESS)
    return RedirectResponse(url=detail_url, status_code=303)
