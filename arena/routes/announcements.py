#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public announcement board pages for the Arena surface, plus the acknowledgment.

The list and detail pages are on the Arena public allowlist
(``arena/dependencies/access_control.py``) and serve the ``arena`` domain only: an
id published on Web answers the same ``404`` an unknown id does. Nothing here can
change a published announcement; the one ``POST`` records that a logged-in user
acknowledged a *required* one, and requires the user itself because the public
prefix means the global gate lets it through.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user, require_arena_user
from arena.models.arena_users import ArenaUser
from arena.routes.safe_redirect import same_origin_referer_path
from shared.enumerations import AnnouncementDomain
from shared.services.announcement_acknowledgment_service import acknowledge_announcement
from shared.services.announcement_service import get_announcement, list_announcements
from shared.services.pagination_service import parse_page

router = APIRouter(prefix="/announcements", tags=["arena-announcements"])

_DOMAIN = AnnouncementDomain.ARENA


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


@router.get("", response_class=HTMLResponse, name="arena_announcement_list")
async def arena_announcement_list(
    request: Request,
    page: str | None = None,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render one page (25 rows) of Arena announcements, newest first."""
    pagination = await list_announcements(session, domain=_DOMAIN, page=parse_page(page))
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "announcements/list.html",
            {"current_user": current_user, "pagination": pagination},
        )
    )


@router.get("/{announcement_id}", response_class=HTMLResponse, name="arena_announcement_detail")
async def arena_announcement_detail(
    request: Request,
    announcement_id: str,
    page: str | None = None,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render one Arena announcement.

    ``page`` is the list page the reader came from; it is carried into the Back
    link with the row fragment so the list reopens on the same page with the
    row highlighted.
    """
    announcement = await get_announcement(session, domain=_DOMAIN, announcement_id=announcement_id)
    if announcement is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    list_url = request.url_for("arena_announcement_list")
    back_url = f"{list_url}?page={parse_page(page)}#announcement-{announcement.id}"
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "announcements/detail.html",
            {"current_user": current_user, "announcement": announcement, "back_url": back_url},
        )
    )


@router.post("/{announcement_id}/acknowledge", name="arena_announcement_acknowledge")
async def arena_announcement_acknowledge(
    request: Request,
    announcement_id: str,
    acknowledge: str | None = Form(None),
    user: ArenaUser = Depends(require_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Record that the current user acknowledged a required announcement.

    The checkbox value is mandatory: a submission without it is a ``400`` and
    writes nothing. The write is idempotent, so a double submit is harmless.
    Afterwards the user goes back to the page the pop-up was on (the same-origin
    ``Referer``, falling back to the dashboard), which renders the next pending
    announcement if there is one.
    """
    if acknowledge is None:
        raise HTTPException(status_code=400, detail="The acknowledgment box must be checked.")
    if not await acknowledge_announcement(session, user_id=user.id, announcement_id=announcement_id):
        raise HTTPException(status_code=404, detail="Announcement not found")
    await session.commit()
    destination = same_origin_referer_path(request, fallback=str(request.url_for("arena_dashboard")))
    return RedirectResponse(url=destination, status_code=303)
