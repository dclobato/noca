#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin dashboard security-event viewer and its CSV export.

Extracted from admin_dashboard_history.py to stay within the 300-line per-file
target.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.admin import require_arena_admin
from arena.models.arena_users import ArenaUser
from arena.services.pagination_service import parse_page
from shared.services.pagination_service import effective_per_page
from shared.services.security_events import list_security_event_filter_values, list_security_events_paginated
from shared.services.security_events_export import csv_filename, stream_security_events_csv

router = APIRouter(prefix="/admin/dashboard", tags=["arena-admin"])

_ALLOWED_PER_PAGE: set[int] = {10, 25, 50, 100, 500}
_DEFAULT_PER_PAGE = 25
# The Arena viewer owns the Arena-side modules; Web events belong to the
# uberadmin viewer. This is an authorization boundary, not a user filter, so the
# CSV export keeps it even though it drops the on-screen filters.
_SECURITY_EVENTS_MODULES = ["arena", "aiassistant"]


def _html(response: Any) -> HTMLResponse:
    """Cast a template response for type-checker satisfaction."""
    return cast(HTMLResponse, response)


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
        allowed=_ALLOWED_PER_PAGE,
        default=_DEFAULT_PER_PAGE,
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


@router.get(
    "/security-events.csv",
    response_class=StreamingResponse,
    name="arena_admin_dashboard_security_events_csv",
)
async def admin_dashboard_security_events_csv(
    request: Request,
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Download every Arena-side security event as CSV, ignoring the on-screen filters.

    The stream reads through the request-scoped session. That is safe because
    FastAPI closes ``yield`` dependencies only after the whole response --
    streaming body included -- has been sent; the belief that it closed them
    first is what once made this route open a second session, and so hold two
    pool connections for the length of every export (#198).
    """

    async def _stream() -> AsyncIterator[str]:
        async for chunk in stream_security_events_csv(session, modules=_SECURITY_EVENTS_MODULES):
            yield chunk

    filename = csv_filename("arena-security-events")
    return StreamingResponse(
        _stream(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
