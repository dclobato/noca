#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin management of the announcement board.

Announcements are immutable once published: this module offers a list, a create
form, creation, and deletion, and deliberately no edit or update route. Arena is
the surface that may mark an announcement as ``required`` -- the flag is stored
here and given its behaviour (the acknowledgment pop-up) by a later slice.
Every mutation is audited through the shared admin-action log on the same
transaction.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.admin import require_arena_admin
from arena.models.arena_users import ArenaUser
from arena.services.required_announcement_cache import invalidate_required_announcements_cache
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

router = APIRouter(prefix="/admin", tags=["arena-admin"])

_DOMAIN = AnnouncementDomain.ARENA
_MODULE = "arena"


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def _list_url(request: Request, *, page: int = 1, anchor: str | None = None) -> str:
    """Build the management list URL, always carrying the page and optionally a row fragment."""
    url = f"{request.url_for('arena_admin_announcement_list')}?page={page}"
    return f"{url}#{anchor}" if anchor else url


def _render_form(
    request: Request,
    admin: ArenaUser,
    *,
    form: dict[str, Any],
    errors: list[str],
) -> HTMLResponse:
    """Render the create form, re-filled with what the admin typed."""
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/announcement_form.html",
            {
                "current_user": admin,
                "form": form,
                "errors": errors,
                "create_url": request.url_for("arena_admin_announcement_create"),
                "back_url": _list_url(request),
            },
        )
    )


@router.get("/announcements", response_class=HTMLResponse, name="arena_admin_announcement_list")
async def arena_admin_announcement_list(
    request: Request,
    page: str | None = None,
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the paginated announcement management list."""
    pagination = await list_announcements(session, domain=_DOMAIN, page=parse_page(page))
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/announcement_list.html",
            {"current_user": admin, "pagination": pagination},
        )
    )


@router.get("/announcements/new", response_class=HTMLResponse, name="arena_admin_announcement_new")
async def arena_admin_announcement_new(
    request: Request,
    admin: ArenaUser = Depends(require_arena_admin),
) -> HTMLResponse:
    """Render the empty create form hosting the statement editor."""
    return _render_form(request, admin, form={"title": "", "body": "", "required": False}, errors=[])


@router.post("/announcements", name="arena_admin_announcement_create")
async def arena_admin_announcement_create(
    request: Request,
    flash: FlashDep,
    title: str = Form(""),
    body: str = Form(""),
    required: str | None = Form(None),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Publish an announcement, or re-render the form with the validation messages.

    A refusal answers ``200`` with the typed values rather than redirecting, so
    the author's Markdown draft is not lost. ``required`` is the form checkbox:
    present means checked. It is chosen here, once, because a published
    announcement is immutable.
    """
    is_required = required is not None
    form = {"title": title, "body": body, "required": is_required}
    try:
        announcement = await create_announcement(
            session,
            domain=_DOMAIN,
            title=title,
            body=body,
            required=is_required,
            published_by_id=admin.id,
            published_by_label=admin.email_normalizado,
        )
    except ValueError as exc:
        return _render_form(request, admin, form=form, errors=[str(exc)])
    await record_announcement_published(
        session,
        request,
        module=_MODULE,
        actor_user_id=admin.id,
        actor_label=admin.email_normalizado,
        announcement=announcement,
    )
    await session.commit()
    if announcement.required:
        invalidate_required_announcements_cache()
    flash(f"Announcement “{announcement.title}” published.", FlashCategory.SUCCESS)
    return RedirectResponse(
        url=_list_url(request, anchor=f"announcement-{announcement.id}"),
        status_code=303,
    )


@router.post("/announcements/{announcement_id}/delete", name="arena_admin_announcement_delete")
async def arena_admin_announcement_delete(
    request: Request,
    announcement_id: str,
    flash: FlashDep,
    page: str = Form("1"),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Remove one Arena announcement and return to the management list.

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
        actor_user_id=admin.id,
        actor_label=admin.email_normalizado,
        announcement=announcement,
    )
    await session.commit()
    if announcement.required:
        invalidate_required_announcements_cache()
    flash(f"Announcement “{announcement.title}” removed.", FlashCategory.SUCCESS)
    return RedirectResponse(url=_list_url(request, page=parse_page(page)), status_code=303)
