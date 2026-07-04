#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin affiliation CRUD routes.

All routes require the ``ARENA_ADMIN`` role via ``require_arena_admin``.
Add/edit/delete are modal-based forms that POST to these endpoints and
redirect back to the paginated list, preserving filter state.

Logo management is handled by a dedicated JSON-returning route
(``POST /admin/affiliations/{id}/logo``) that is called via AJAX from the
standalone logo modal, keeping logo upload completely separate from the
create/edit flow and avoiding Bootstrap nested-modal issues.
"""

from typing import Any, cast

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.admin import require_arena_admin
from arena.models.arena_affiliations import ArenaAffiliation
from arena.models.arena_users import ArenaUser
from arena.services import admin_affiliation_service
from arena.services.pagination_service import parse_page
from arena.services.profile_location_service import list_countries, list_subdivisions
from shared.services.admin_audit import record_admin_action
from shared.services.imageprocessing_service import ImageProcessingError

router = APIRouter(prefix="/admin", tags=["arena-admin"])

_LOGO_MAX_FILE_SIZE = 2 * 1024 * 1024


def _html(response: Any) -> HTMLResponse:
    """Cast TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def _affiliation_list_url(
    request: Request,
    *,
    page: str = "1",
    per_page: str = "25",
    search: str = "",
    country_code: str = "",
    subdivision_code: str = "",
    anchor: str | None = None,
) -> str:
    """Build an affiliation list URL preserving all active filter state.

    Args:
        request: Current HTTP request.
        page: Current page number; omitted when ``"1"``.
        per_page: Rows per page; omitted when equal to the default.
        search: Name search fragment.
        country_code: Country filter value.
        subdivision_code: Subdivision filter value.
        anchor: Optional fragment identifier (without ``#``) to append.

    Returns:
        Fully qualified affiliation list URL, optionally with ``#anchor``.
    """
    params: dict[str, str] = {}
    if page and page != "1":
        params["page"] = page
    if per_page and per_page != str(admin_affiliation_service.DEFAULT_PER_PAGE):
        params["per_page"] = per_page
    if search and search.strip():
        params["search"] = search.strip()
    if country_code and country_code.strip():
        params["country_code"] = country_code.strip().upper()
        if subdivision_code and subdivision_code.strip():
            params["subdivision_code"] = subdivision_code.strip().upper()
    base_url = str(request.url_for("arena_admin_affiliation_list"))
    url = f"{base_url}?{'&'.join(f'{k}={v}' for k, v in params.items())}" if params else base_url
    return f"{url}#{anchor}" if anchor else url


async def _get_affiliation_or_404(affiliation_id: str, session: AsyncSession) -> ArenaAffiliation:
    """Fetch an Arena affiliation by id, raising HTTP 404 if not found."""
    affiliation = await admin_affiliation_service.get_affiliation(session, affiliation_id)
    if affiliation is None:
        raise HTTPException(status_code=404, detail="Affiliation not found")
    return affiliation


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("/affiliations", response_class=HTMLResponse, name="arena_admin_affiliation_list")
async def admin_affiliation_list(
    request: Request,
    flash: FlashDep,
    page: str | None = None,
    per_page: str | None = None,
    search: str | None = None,
    country_code: str | None = None,
    subdivision_code: str | None = None,
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the paginated Arena affiliation list with filters."""
    effective_per_page = admin_affiliation_service.effective_per_page(
        int(per_page) if per_page and per_page.isdigit() else None
    )
    clean_country = (country_code or "").strip().upper() or None
    clean_subdivision = (subdivision_code or "").strip().upper() or None if clean_country else None

    pagination = await admin_affiliation_service.list_affiliations_paginated(
        session,
        page=parse_page(page),
        per_page=effective_per_page,
        search=search or "",
        country_code=clean_country,
        subdivision_code=clean_subdivision,
    )

    countries = list_countries()
    try:
        subdivisions = list_subdivisions(clean_country) if clean_country else []
    except ValueError:
        subdivisions = []
        clean_country = None
        clean_subdivision = None

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/affiliation_list.html",
            {
                "pagination": pagination,
                "per_page": effective_per_page,
                "search": search or "",
                "country_code": clean_country or "",
                "subdivision_code": clean_subdivision or "",
                "countries": countries,
                "subdivisions": subdivisions,
                "current_user": admin,
            },
        )
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post("/affiliations/new", name="arena_admin_affiliation_create")
async def admin_affiliation_create(
    request: Request,
    flash: FlashDep,
    name: str = Form(""),
    url: str = Form(""),
    country_code: str = Form(""),
    subdivision_code: str = Form(""),
    page: str = Form("1"),
    per_page: str = Form("25"),
    search: str = Form(""),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Create an Arena affiliation from submitted form data."""
    list_url = _affiliation_list_url(
        request,
        page=page,
        per_page=per_page,
        search=search,
        country_code=country_code,
        subdivision_code=subdivision_code,
    )
    try:
        affiliation = await admin_affiliation_service.create_affiliation(
            session,
            name=name,
            url=url or None,
            country_code=country_code or None,
            subdivision_code=subdivision_code or None,
        )
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(url=list_url, status_code=303)

    await session.commit()
    flash(f"Affiliation '{affiliation.name}' created.", FlashCategory.SUCCESS)
    return RedirectResponse(
        url=_affiliation_list_url(
            request,
            page=page,
            per_page=per_page,
            search=search,
            country_code=country_code,
            subdivision_code=subdivision_code,
            anchor=affiliation.id,
        ),
        status_code=303,
    )


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------


@router.post("/affiliations/{affiliation_id}/edit", name="arena_admin_affiliation_update")
async def admin_affiliation_update(
    request: Request,
    affiliation_id: str,
    flash: FlashDep,
    name: str = Form(""),
    url: str = Form(""),
    country_code: str = Form(""),
    subdivision_code: str = Form(""),
    exclude_from_ranking: bool = Form(False),
    page: str = Form("1"),
    per_page: str = Form("25"),
    search: str = Form(""),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Update an Arena affiliation from submitted form data."""
    affiliation = await _get_affiliation_or_404(affiliation_id, session)
    list_url = _affiliation_list_url(
        request,
        page=page,
        per_page=per_page,
        search=search,
        country_code=country_code,
        subdivision_code=subdivision_code,
    )
    try:
        await admin_affiliation_service.update_affiliation(
            session,
            affiliation,
            name=name,
            url=url or None,
            country_code=country_code or None,
            subdivision_code=subdivision_code or None,
            exclude_from_ranking=exclude_from_ranking,
        )
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(url=list_url, status_code=303)

    await session.commit()
    flash(f"Affiliation '{affiliation.name}' updated.", FlashCategory.SUCCESS)
    return RedirectResponse(
        url=_affiliation_list_url(
            request,
            page=page,
            per_page=per_page,
            search=search,
            country_code=country_code,
            subdivision_code=subdivision_code,
            anchor=affiliation_id,
        ),
        status_code=303,
    )


# ---------------------------------------------------------------------------
# Logo
# ---------------------------------------------------------------------------


@router.post(
    "/affiliations/{affiliation_id}/logo",
    name="arena_admin_affiliation_logo",
    response_class=JSONResponse,
)
async def admin_affiliation_logo(
    request: Request,
    affiliation_id: str,
    foto_cropada: UploadFile | None = File(None),
    remove_logo: str = Form(""),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Set or clear an affiliation logo via AJAX.

    Accepts ``multipart/form-data`` with either:
    - ``foto_cropada`` (file) — new/replacement logo blob (pre-cropped client-side), or
    - ``remove_logo=1`` — flag to clear the existing logo.

    Returns JSON ``{"ok": true, "logo_url": "<url_or_empty_string>"}`` so the
    frontend can update the row thumbnail without a page reload.
    """
    affiliation = await _get_affiliation_or_404(affiliation_id, session)

    if remove_logo == "1":
        await admin_affiliation_service.clear_logo(session, affiliation)
        await session.commit()
        return JSONResponse({"ok": True, "logo_url": ""})

    if foto_cropada and foto_cropada.filename:
        image_service = request.app.state.image_service
        try:
            processed = await image_service.process_upload_image(
                upload=foto_cropada,
                avatar_size=64,
                max_file_size=_LOGO_MAX_FILE_SIZE,
                crop_aspect_ratio=True,
                aspect_width=1,
                aspect_height=1,
            )
            await admin_affiliation_service.set_logo(
                session,
                affiliation,
                logo_base64=processed.imagem_base64,
                logo_mime=processed.mime_type,
                logo_thumbnail_base64=processed.avatar_base64,
            )
        except (ImageProcessingError, ValueError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        await session.commit()
        logo_url = str(request.url_for("arena_affiliation_logo_thumbnail", affiliation_id=affiliation_id))
        return JSONResponse({"ok": True, "logo_url": logo_url})

    return JSONResponse({"ok": False, "error": "No action provided."}, status_code=400)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@router.post("/affiliations/{affiliation_id}/delete", name="arena_admin_affiliation_delete")
async def admin_affiliation_delete(
    request: Request,
    affiliation_id: str,
    flash: FlashDep,
    page: str = Form("1"),
    per_page: str = Form("25"),
    search: str = Form(""),
    country_code: str = Form(""),
    subdivision_code: str = Form(""),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Delete an Arena affiliation, detaching linked users, and return to the list."""
    affiliation = await _get_affiliation_or_404(affiliation_id, session)
    affiliation_name = affiliation.name
    await admin_affiliation_service.delete_affiliation(session, affiliation)
    await record_admin_action(
        session,
        request,
        module="arena",
        actor_user_id=admin.id,
        action="delete",
        target_type="arena_affiliation",
        target_id=affiliation_id,
        detail=f"name={affiliation_name}",
    )
    await session.commit()
    flash(f"Affiliation '{affiliation_name}' removed.", FlashCategory.SUCCESS)
    return RedirectResponse(
        url=_affiliation_list_url(
            request,
            page=page,
            per_page=per_page,
            search=search,
            country_code=country_code,
            subdivision_code=subdivision_code,
        ),
        status_code=303,
    )
