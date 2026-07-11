#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin dashboard history sub-pages: global login history and submissions.

Extracted from admin_dashboard.py to stay within the 300-line per-file target.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.admin import require_arena_admin
from arena.models.arena_users import ArenaUser
from arena.routes.admin_date_helpers import _effective_per_page, _local_midnight_to_utc, _parse_date_param
from arena.services import admin_login_history_service, admin_submission_service
from arena.services.pagination_service import parse_page
from arena.services.user_timezone_service import timezone_name_for_user
from shared.db_schema import languages as languages_table
from shared.db_schema.arena import arena_submissions
from shared.enumerations import JudgmentStatus, Verdict
from shared.services.pagination_service import effective_per_page
from shared.services.security_events import list_security_event_filter_values, list_security_events_paginated
from shared.services.valkey_service.queue_ops import enqueue_arena_submission_job

router = APIRouter(prefix="/admin/dashboard", tags=["arena-admin"])

_HISTORY_ALLOWED_PER_PAGE: set[int] = {10, 25, 50, 100, 500}
_HISTORY_DEFAULT_PER_PAGE = 25

_SUBMISSIONS_ALLOWED_PER_PAGE: set[int] = {10, 25, 50, 100, 500}
_SUBMISSIONS_DEFAULT_PER_PAGE = 25

_SECURITY_EVENTS_ALLOWED_PER_PAGE: set[int] = {10, 25, 50, 100, 500}
_SECURITY_EVENTS_DEFAULT_PER_PAGE = 25
_SECURITY_EVENTS_MODULES = ["arena", "aiassistant"]
_SUBMISSION_STATUS_FILTERS: tuple[JudgmentStatus, ...] = (
    JudgmentStatus.QUEUED,
    JudgmentStatus.DISPATCHED,
    JudgmentStatus.JUDGING,
    JudgmentStatus.DONE,
    JudgmentStatus.FAILED,
)


def _html(response: Any) -> HTMLResponse:
    """Cast a template response for type-checker satisfaction."""
    return cast(HTMLResponse, response)


@router.get("/login-history", response_class=HTMLResponse, name="arena_admin_dashboard_login_history")
async def admin_dashboard_login_history(
    request: Request,
    page: str | None = None,
    per_page: str | None = None,
    sort_dir: str = "desc",
    date_from: str | None = None,
    date_to: str | None = None,
    search: str = "",
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the global Arena login history page across all users."""
    if sort_dir != "asc":
        sort_dir = "desc"
    resolved_per_page = _effective_per_page(
        per_page, allowed=_HISTORY_ALLOWED_PER_PAGE, default=_HISTORY_DEFAULT_PER_PAGE
    )
    resolved_page = parse_page(page)

    parsed_from = _parse_date_param(date_from)
    parsed_to = _parse_date_param(date_to)
    tz_name = timezone_name_for_user(admin)
    date_from_utc = _local_midnight_to_utc(parsed_from, tz_name) if parsed_from else None
    date_to_utc = _local_midnight_to_utc(parsed_to + timedelta(days=1), tz_name) if parsed_to else None

    login_history = await admin_login_history_service.list_global_login_history_paginated(
        session,
        page=resolved_page,
        per_page=resolved_per_page,
        sort_dir=sort_dir,
        date_from_utc=date_from_utc,
        date_to_utc=date_to_utc,
        search=search,
    )

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/dashboard_login_history.html",
            {
                "current_user": admin,
                "login_history": login_history,
                "per_page": resolved_per_page,
                "sort_dir": sort_dir,
                "date_from": date_from or "",
                "date_to": date_to or "",
                "search": search,
            },
        )
    )


@router.get("/submissions", response_class=HTMLResponse, name="arena_admin_dashboard_submissions")
async def admin_dashboard_submissions(
    request: Request,
    page: str | None = None,
    per_page: str | None = None,
    search: str = "",
    verdict_filter: str = "",
    status_filter: str = "",
    ai_filter: str = "",
    language_filter: str = "",
    problem_filter: str = "",
    sort_dir: str = "desc",
    date_from: str | None = None,
    date_to: str | None = None,
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the admin submission list page across all Arena users."""
    if sort_dir != "asc":
        sort_dir = "desc"
    resolved_per_page = _effective_per_page(
        per_page, allowed=_SUBMISSIONS_ALLOWED_PER_PAGE, default=_SUBMISSIONS_DEFAULT_PER_PAGE
    )
    resolved_page = parse_page(page)

    # Validate verdict_filter against known values
    valid_verdicts = {v.value for v in Verdict}
    valid_statuses = {status.value for status in _SUBMISSION_STATUS_FILTERS}
    eff_verdict = verdict_filter if verdict_filter in valid_verdicts else None
    eff_status = status_filter if status_filter in valid_statuses else None
    eff_ai = ai_filter if ai_filter in ("yes", "no") else None
    eff_language = language_filter or None
    eff_problem = problem_filter or None

    parsed_from = _parse_date_param(date_from)
    parsed_to = _parse_date_param(date_to)
    tz_name = timezone_name_for_user(admin)
    date_from_utc = _local_midnight_to_utc(parsed_from, tz_name) if parsed_from else None
    date_to_utc = _local_midnight_to_utc(parsed_to + timedelta(days=1), tz_name) if parsed_to else None

    submissions = await admin_submission_service.list_submissions_paginated(
        session,
        page=resolved_page,
        per_page=resolved_per_page,
        search=search,
        verdict_filter=eff_verdict,
        status_filter=eff_status,
        ai_filter=eff_ai,
        language_filter=eff_language,
        problem_filter=eff_problem,
        sort_dir=sort_dir,
        date_from_utc=date_from_utc,
        date_to_utc=date_to_utc,
    )

    # Build language list for filter dropdown from submissions that exist
    lang_rows = (
        await session.execute(
            select(distinct(arena_submissions.c.language_id), languages_table.c.name)
            .join(languages_table, arena_submissions.c.language_id == languages_table.c.id)
            .order_by(languages_table.c.name)
        )
    ).all()
    available_languages = [(row[0], row[1]) for row in lang_rows]

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/dashboard_submissions.html",
            {
                "current_user": admin,
                "submissions": submissions,
                "per_page": resolved_per_page,
                "search": search,
                "verdict_filter": verdict_filter,
                "status_filter": status_filter,
                "ai_filter": ai_filter,
                "language_filter": language_filter,
                "problem_filter": problem_filter,
                "sort_dir": sort_dir,
                "date_from": date_from or "",
                "date_to": date_to or "",
                "available_languages": available_languages,
                "Verdict": Verdict,
                "submission_status_filters": _SUBMISSION_STATUS_FILTERS,
            },
        )
    )


@router.post(
    "/submissions/{submission_id}/reenqueue",
    response_class=HTMLResponse,
    name="arena_admin_dashboard_submission_reenqueue",
)
async def admin_dashboard_submission_reenqueue(
    request: Request,
    submission_id: str,
    flash: FlashDep,
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Re-enqueue a submission whose latest judgment failed with an internal error."""
    job = await admin_submission_service.reenqueue_failed_submission(session, submission_id=submission_id)
    if job is None:
        await session.rollback()
        flash("Submission not found or not in a failed state.", FlashCategory.DANGER)
    else:
        await session.commit()
        await enqueue_arena_submission_job(request.app.state.valkey_runtime, job)
        flash("Submission re-enqueued for judging.", FlashCategory.SUCCESS)

    referer = request.headers.get("referer")
    redirect_url = referer or str(request.url_for("arena_admin_dashboard_submissions"))
    return RedirectResponse(url=redirect_url, status_code=303)


@router.get("/security-events", response_class=HTMLResponse, name="arena_admin_dashboard_security_events")
async def admin_dashboard_security_events(
    request: Request,
    page: str | None = None,
    per_page: str | None = None,
    module: str = "",
    event_type: str = "",
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render paginated Arena-side security events (arena and its AI worker)."""
    resolved_page = parse_page(page)
    resolved_per_page = effective_per_page(
        per_page,
        allowed=_SECURITY_EVENTS_ALLOWED_PER_PAGE,
        default=_SECURITY_EVENTS_DEFAULT_PER_PAGE,
    )
    selected_module = module.strip()
    if selected_module not in _SECURITY_EVENTS_MODULES:
        selected_module = ""
    event_modules = [selected_module] if selected_module else _SECURITY_EVENTS_MODULES
    event_type_filter = event_type.strip() or None
    filter_values = await list_security_event_filter_values(session, modules=event_modules)
    events = await list_security_events_paginated(
        session,
        page=resolved_page,
        per_page=resolved_per_page,
        modules=event_modules,
        event_type=event_type_filter,
    )
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/dashboard_security_events.html",
            {
                "current_user": admin,
                "security_events": events,
                "available_modules": _SECURITY_EVENTS_MODULES,
                "selected_module": selected_module,
                "available_event_types": filter_values.event_types,
                "selected_event_type": event_type_filter or "",
                "per_page": resolved_per_page,
            },
        )
    )
