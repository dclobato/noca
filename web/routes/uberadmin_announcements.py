#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""UberAdmin management of the Web announcement board.

Announcements are immutable once published: this module offers a list, a create
form, creation, and deletion, and deliberately no edit or update route. Every
mutation is audited through the shared admin-action log on the same transaction.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import AnnouncementDomain
from shared.services.announcement_service import (
    create_announcement,
    delete_announcement,
    get_announcement,
    list_announcements,
    record_announcement_deleted,
    record_announcement_published,
)
from shared.services.pagination_service import parse_page
from web.database import get_db
from web.dependencies import get_uberadmin
from web.models.users import UberAdmin

router = APIRouter(prefix="/uberadmin", tags=["uberadmin"])

_DOMAIN = AnnouncementDomain.WEB
_MODULE = "web"


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[no-any-return]


def _list_url(request: Request, *, page: int = 1, anchor: str | None = None) -> str:
    """Build the management list URL, always carrying the page and optionally a row fragment."""
    url = f"{request.url_for('uberadmin_announcements')}?page={page}"
    return f"{url}#{anchor}" if anchor else url


def _render_form(
    request: Request,
    uberadmin: UberAdmin,
    *,
    form: dict[str, Any],
    errors: list[str],
) -> HTMLResponse:
    """Render the create form, re-filled with what the admin typed."""
    return _templates(request).TemplateResponse(
        request,
        "uberadmin/announcement_form.html",
        {
            "current_user": uberadmin,
            "form": form,
            "errors": errors,
            "create_url": request.url_for("uberadmin_announcement_create"),
            "back_url": _list_url(request),
        },
    )


@router.get("/announcements", response_class=HTMLResponse, name="uberadmin_announcements")
async def uberadmin_announcements(
    request: Request,
    page: str | None = None,
    uberadmin: UberAdmin = Depends(get_uberadmin),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the paginated announcement management list."""
    pagination = await list_announcements(session, domain=_DOMAIN, page=parse_page(page))
    return _templates(request).TemplateResponse(
        request,
        "uberadmin/announcements.html",
        {"current_user": uberadmin, "pagination": pagination},
    )


@router.get("/announcements/new", response_class=HTMLResponse, name="uberadmin_announcement_new")
async def uberadmin_announcement_new(
    request: Request,
    uberadmin: UberAdmin = Depends(get_uberadmin),
) -> HTMLResponse:
    """Render the empty create form hosting the statement editor."""
    return _render_form(request, uberadmin, form={"title": "", "body": ""}, errors=[])


@router.post("/announcements", name="uberadmin_announcement_create")
async def uberadmin_announcement_create(
    request: Request,
    flash: FlashDep,
    title: str = Form(""),
    body: str = Form(""),
    uberadmin: UberAdmin = Depends(get_uberadmin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Publish an announcement, or re-render the form with the validation messages.

    A refusal answers ``200`` with the typed values rather than redirecting, so
    the author's Markdown draft is not lost. ``required`` is fixed to ``False``
    on this surface and never read from the form.
    """
    try:
        announcement = await create_announcement(
            session,
            domain=_DOMAIN,
            title=title,
            body=body,
            required=False,
            published_by_id=uberadmin.id,
            published_by_label=uberadmin.username,
        )
    except ValueError as exc:
        return _render_form(request, uberadmin, form={"title": title, "body": body}, errors=[str(exc)])
    await record_announcement_published(
        session,
        request,
        module=_MODULE,
        actor_user_id=uberadmin.id,
        actor_label=uberadmin.username,
        announcement=announcement,
    )
    await session.commit()
    flash(f"Announcement “{announcement.title}” published.", FlashCategory.SUCCESS)
    return RedirectResponse(
        url=_list_url(request, anchor=f"announcement-{announcement.id}"),
        status_code=303,
    )


@router.post("/announcements/{announcement_id}/delete", name="uberadmin_announcement_delete")
async def uberadmin_announcement_delete(
    request: Request,
    announcement_id: str,
    flash: FlashDep,
    page: str = Form("1"),
    uberadmin: UberAdmin = Depends(get_uberadmin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Remove one Web announcement and return to the management list.

    The warning-level audit row is written only when the ``DELETE`` actually
    removed a row: an announcement deleted concurrently between the lookup and
    the statement answers ``404`` and leaves no audit trace of a deletion that
    did not happen.
    """
    announcement = await get_announcement(session, domain=_DOMAIN, announcement_id=announcement_id)
    if announcement is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    if not await delete_announcement(session, domain=_DOMAIN, announcement_id=announcement_id):
        raise HTTPException(status_code=404, detail="Announcement not found")
    await record_announcement_deleted(
        session,
        request,
        module=_MODULE,
        actor_user_id=uberadmin.id,
        actor_label=uberadmin.username,
        announcement=announcement,
    )
    await session.commit()
    flash(f"Announcement “{announcement.title}” removed.", FlashCategory.SUCCESS)
    return RedirectResponse(url=_list_url(request, page=parse_page(page)), status_code=303)
