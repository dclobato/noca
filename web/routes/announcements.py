#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public (anonymous) announcement board pages for the Web surface.

Both routes are on the Web public allowlist (``web/dependencies.py``) and serve
the ``web`` domain only: an id published on Arena answers the same ``404`` an
unknown id does. Only ``GET`` is registered, so there is no way to change a
published announcement through this module.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from shared.enumerations import AnnouncementDomain
from shared.services.announcement_service import get_announcement, list_announcements
from shared.services.pagination_service import parse_page

router = APIRouter(tags=["announcements"])

_DOMAIN = AnnouncementDomain.WEB


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[no-any-return]


@router.get("/announcements", response_class=HTMLResponse, name="announcements_list")
async def announcements_list(request: Request, page: str | None = None) -> HTMLResponse:
    """Render one page (25 rows) of Web announcements, newest first."""
    async with request.app.state.db_session() as session:
        pagination = await list_announcements(session, domain=_DOMAIN, page=parse_page(page))
    return _templates(request).TemplateResponse(
        request,
        "announcements/list.html",
        {"pagination": pagination},
    )


@router.get(
    "/announcements/{announcement_id}",
    response_class=HTMLResponse,
    name="announcement_detail",
)
async def announcement_detail(
    request: Request,
    announcement_id: str,
    page: str | None = None,
) -> HTMLResponse:
    """Render one Web announcement.

    ``page`` is the list page the reader came from; it is carried into the Back
    link together with the row fragment so the list reopens on the same page
    with the row highlighted.
    """
    async with request.app.state.db_session() as session:
        announcement = await get_announcement(session, domain=_DOMAIN, announcement_id=announcement_id)
    if announcement is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    list_url = request.url_for("announcements_list")
    back_url = f"{list_url}?page={parse_page(page)}#announcement-{announcement.id}"
    return _templates(request).TemplateResponse(
        request,
        "announcements/detail.html",
        {"announcement": announcement, "back_url": back_url},
    )
