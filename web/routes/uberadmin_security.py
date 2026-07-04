#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""UberAdmin viewer for the cross-module security-event log."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from shared.services.pagination_service import effective_per_page, parse_page
from shared.services.security_events import (
    list_security_event_filter_values,
    list_security_events_paginated,
)
from web.dependencies import get_uberadmin
from web.models.users import UberAdmin

router = APIRouter(prefix="/uberadmin", tags=["uberadmin"])

_ALLOWED_PER_PAGE: set[int] = {10, 25, 50, 100, 500}
_DEFAULT_PER_PAGE = 25
# The Web viewer is scoped to Web-produced events only. Arena/aiassistant events
# live in the same shared table but belong to the Arena admin viewer.
_VIEWER_MODULE = "web"


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[no-any-return]


@router.get("/security-events", response_class=HTMLResponse, name="uberadmin_security_events")
async def security_events(
    request: Request,
    uberadmin: UberAdmin = Depends(get_uberadmin),
    event_type: str = Query(""),
    page: str | None = None,
    per_page: str | None = None,
) -> HTMLResponse:
    """Render paginated Web security events with an optional event-type filter."""
    event_type_filter = event_type.strip() or None
    resolved_page = parse_page(page)
    resolved_per_page = effective_per_page(
        per_page,
        allowed=_ALLOWED_PER_PAGE,
        default=_DEFAULT_PER_PAGE,
    )
    async with request.app.state.db_session() as session:
        filter_values = await list_security_event_filter_values(session, module=_VIEWER_MODULE)
        events = await list_security_events_paginated(
            session,
            page=resolved_page,
            per_page=resolved_per_page,
            module=_VIEWER_MODULE,
            event_type=event_type_filter,
        )

    return _templates(request).TemplateResponse(
        request,
        "uberadmin/security_events.html",
        {
            "current_user": uberadmin,
            "security_events": events,
            "available_event_types": filter_values.event_types,
            "selected_event_type": event_type_filter or "",
            "per_page": resolved_per_page,
            "filters_active": bool(event_type_filter) or resolved_per_page != _DEFAULT_PER_PAGE,
        },
    )
