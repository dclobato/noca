#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena administration dashboard routes."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.database import get_db
from arena.dependencies.admin import require_arena_admin
from arena.models.arena_users import ArenaUser
from arena.routes.admin_date_helpers import _effective_per_page, _local_midnight_to_utc, _parse_date_param
from arena.services import admin_ai_credits_service, admin_worker_service, ai_turnaround_stats_service
from arena.services.pagination_service import parse_page
from arena.services.user_timezone_service import timezone_name_for_user
from arena.services.valkey_service import WorkerClass
from shared.services.valkey_service import WorkerCommandType

_AI_CREDITS_ALLOWED_PER_PAGE: set[int] = {10, 25, 50, 100, 500}
_AI_CREDITS_DEFAULT_PER_PAGE = 25


router = APIRouter(prefix="/admin/dashboard", tags=["arena-admin"])


def _html(response: Any) -> HTMLResponse:
    """Cast a template response for type-checker satisfaction."""
    return cast(HTMLResponse, response)


async def _render_worker_cards(
    request: Request,
    admin: ArenaUser,
    session: AsyncSession,
    *,
    include_flash_oob: bool = False,
) -> HTMLResponse:
    """Render the HTMX-refreshable worker cards fragment."""
    worker_cards = await admin_worker_service.list_worker_cards(
        session,
        request.app.state.valkey_runtime,
        secret=settings.WORKER_COMMAND_SECRET,
    )
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/_worker_cards.html",
            {
                "current_user": admin,
                "worker_cards": worker_cards,
                "include_flash_oob": include_flash_oob,
            },
        )
    )


@router.get("", response_class=HTMLResponse, name="arena_admin_dashboard")
async def admin_dashboard(
    request: Request,
    admin: ArenaUser = Depends(require_arena_admin),
) -> Response:
    """Render the Admin Dashboard landing page with sub-section links."""
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/dashboard.html",
            {"current_user": admin},
        )
    )


@router.get("/service-status", response_class=HTMLResponse, name="arena_admin_dashboard_service_status")
async def admin_dashboard_service_status(
    request: Request,
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the Service Status sub-page showing worker presence cards."""
    worker_cards = await admin_worker_service.list_worker_cards(
        session,
        request.app.state.valkey_runtime,
        secret=settings.WORKER_COMMAND_SECRET,
    )
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/dashboard_service_status.html",
            {
                "current_user": admin,
                "worker_cards": worker_cards,
            },
        )
    )


@router.get("/workers", response_class=HTMLResponse, name="arena_admin_dashboard_workers")
async def admin_dashboard_workers(
    request: Request,
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Return the worker-card fragment polled by HTMX."""
    return await _render_worker_cards(request, admin, session)


@router.post("/workers/remove", response_class=HTMLResponse, name="arena_admin_dashboard_worker_remove")
async def admin_dashboard_worker_remove(
    request: Request,
    worker_class: str = Form(...),
    worker_id: str = Form(...),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Remove one worker from the dashboard until its next heartbeat."""
    resolved_class = _resolve_class(worker_class)

    await admin_worker_service.remove_worker_from_dashboard(
        request.app.state.valkey_runtime,
        worker_class=resolved_class,
        worker_id=worker_id,
    )
    if request.headers.get("HX-Request") == "true":
        return await _render_worker_cards(request, admin, session)
    return RedirectResponse(request.url_for("arena_admin_dashboard_service_status"), status_code=303)


@router.post("/workers/pause", response_class=HTMLResponse, name="arena_admin_dashboard_worker_pause")
async def admin_dashboard_worker_pause(
    request: Request,
    flash: FlashDep,
    worker_class: str = Form(...),
    worker_id: str = Form(...),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Pause a queue-consuming worker."""
    return await _pause_or_resume(request, flash, worker_class, worker_id, admin, session, pause=True)


@router.post("/workers/resume", response_class=HTMLResponse, name="arena_admin_dashboard_worker_resume")
async def admin_dashboard_worker_resume(
    request: Request,
    flash: FlashDep,
    worker_class: str = Form(...),
    worker_id: str = Form(...),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Resume a previously paused queue-consuming worker."""
    return await _pause_or_resume(request, flash, worker_class, worker_id, admin, session, pause=False)


async def _pause_or_resume(
    request: Request,
    flash: FlashDep,
    worker_class: str,
    worker_id: str,
    admin: ArenaUser,
    session: AsyncSession,
    *,
    pause: bool,
) -> Response:
    """Shared handler for the pause and resume endpoints."""
    action = admin_worker_service.pause_worker if pause else admin_worker_service.resume_worker
    verb = "paused" if pause else "resumed"

    result = await action(
        session,
        request.app.state.valkey_runtime,
        worker_class=worker_class,
        worker_id=worker_id,
        paused_by=admin.email,
        actor_user_id=admin.id,
        secret=settings.WORKER_COMMAND_SECRET,
    )

    if result.outcome == "rejected_bad_request":
        raise HTTPException(status_code=400, detail="This worker class cannot be paused")

    if result.outcome == "rejected_disabled":
        flash("Worker pause/resume is disabled (no command secret configured).", FlashCategory.DANGER)
    elif result.transport_status == "transport_failed":
        flash(
            f"Worker {worker_id} {verb}. The live nudge could not be delivered; "
            "the worker will apply it on its next poll.",
            FlashCategory.WARNING,
        )
    else:
        flash(f"Worker {worker_id} {verb}.", FlashCategory.SUCCESS)

    if request.headers.get("HX-Request") == "true":
        return await _render_worker_cards(request, admin, session, include_flash_oob=True)
    return RedirectResponse(request.url_for("arena_admin_dashboard_service_status"), status_code=303)


@router.post("/workers/flush-now", response_class=HTMLResponse, name="arena_admin_dashboard_worker_flush_now")
async def admin_dashboard_worker_flush_now(
    request: Request,
    flash: FlashDep,
    worker_class: str = Form(...),
    worker_id: str = Form(...),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Send a one-shot flush-now trigger to an AI assistant worker."""
    return await _trigger(
        request,
        flash,
        worker_class,
        worker_id,
        admin,
        session,
        action=WorkerCommandType.FLUSH_NOW,
    )


@router.post("/workers/poll-now", response_class=HTMLResponse, name="arena_admin_dashboard_worker_poll_now")
async def admin_dashboard_worker_poll_now(
    request: Request,
    flash: FlashDep,
    worker_class: str = Form(...),
    worker_id: str = Form(...),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Send a one-shot poll-now trigger to an AI assistant worker."""
    return await _trigger(
        request,
        flash,
        worker_class,
        worker_id,
        admin,
        session,
        action=WorkerCommandType.POLL_NOW,
    )


async def _trigger(
    request: Request,
    flash: FlashDep,
    worker_class: str,
    worker_id: str,
    admin: ArenaUser,
    session: AsyncSession,
    *,
    action: WorkerCommandType,
) -> Response:
    """Shared handler for flush-now and poll-now trigger endpoints."""
    verb = "Flush-now" if action is WorkerCommandType.FLUSH_NOW else "Poll-now"

    result = await admin_worker_service.trigger_worker(
        session,
        request.app.state.valkey_runtime,
        worker_class=worker_class,
        worker_id=worker_id,
        triggered_by=admin.email,
        actor_user_id=admin.id,
        action=action,
        secret=settings.WORKER_COMMAND_SECRET,
    )

    if result.outcome == "rejected_bad_request":
        raise HTTPException(status_code=400, detail="This worker class does not support trigger commands")

    if result.outcome == "rejected_disabled":
        flash("Worker trigger commands are disabled (no command secret configured).", FlashCategory.DANGER)
    elif result.transport_status == "transport_failed":
        flash(
            f"{verb} trigger sent to {worker_id}. The worker will not wake early (delivery failed).",
            FlashCategory.WARNING,
        )
    else:
        flash(f"{verb} trigger sent to {worker_id}.", FlashCategory.SUCCESS)

    if request.headers.get("HX-Request") == "true":
        return await _render_worker_cards(request, admin, session, include_flash_oob=True)
    return RedirectResponse(request.url_for("arena_admin_dashboard_service_status"), status_code=303)


@router.get("/ai-credits", response_class=HTMLResponse, name="arena_admin_dashboard_ai_credits")
async def admin_dashboard_ai_credits(
    request: Request,
    page: str | None = None,
    per_page: str | None = None,
    search: str = "",
    sort_dir: str = "desc",
    date_from: str | None = None,
    date_to: str | None = None,
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the AI credits usage page showing all consumption transactions."""
    if sort_dir != "asc":
        sort_dir = "desc"
    resolved_per_page = _effective_per_page(
        per_page, allowed=_AI_CREDITS_ALLOWED_PER_PAGE, default=_AI_CREDITS_DEFAULT_PER_PAGE
    )
    resolved_page = parse_page(page)

    parsed_from = _parse_date_param(date_from)
    parsed_to = _parse_date_param(date_to)
    tz_name = timezone_name_for_user(admin)
    date_from_utc = _local_midnight_to_utc(parsed_from, tz_name) if parsed_from else None
    date_to_utc = _local_midnight_to_utc(parsed_to + timedelta(days=1), tz_name) if parsed_to else None

    transactions = await admin_ai_credits_service.list_consumption_transactions_paginated(
        session,
        page=resolved_page,
        per_page=resolved_per_page,
        search=search,
        sort_dir=sort_dir,
        date_from_utc=date_from_utc,
        date_to_utc=date_to_utc,
    )
    turnaround_seconds = await admin_ai_credits_service.get_batch_turnaround_seconds(
        session,
        {tx.submission_id for tx in transactions.items if tx.submission_id is not None},
    )
    ai_turnaround_stats = await ai_turnaround_stats_service.get_batch_turnaround_stats(request.app.state.valkey_runtime)
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/dashboard_ai_credits.html",
            {
                "current_user": admin,
                "transactions": transactions,
                "turnaround_seconds": turnaround_seconds,
                "ai_turnaround_stats": ai_turnaround_stats,
                "search": search,
                "sort_dir": sort_dir,
                "per_page": resolved_per_page,
                "date_from": date_from or "",
                "date_to": date_to or "",
            },
        )
    )


def _resolve_class(worker_class: str) -> WorkerClass:
    """Resolve a worker-class string or raise an HTTP 400."""
    try:
        return WorkerClass(worker_class)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid worker class") from exc
