#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin collection CRUD routes.

A collection is an event (ICPC, Maratona SBC, InterIF) or a class (Iniciantes,
Expressoes regulares), and a problem belongs to at most one. Deleting a
collection unfiles its problems rather than deleting them.
"""

from typing import Any, cast
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.admin import require_arena_admin
from arena.models.arena_problems import ArenaCollection
from arena.models.arena_users import ArenaUser
from arena.services import admin_collection_service
from arena.services.pagination_service import parse_page
from shared.services.admin_audit import record_admin_action

router = APIRouter(prefix="/admin", tags=["arena-admin"])

_ALLOWED_PER_PAGE = [10, 25, 50, 100]
_DEFAULT_PER_PAGE = 25


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def _collection_list_url(
    request: Request,
    *,
    page: str = "1",
    per_page: str = "25",
    search: str | None = None,
    anchor: str | None = None,
) -> str:
    """Build a collection list URL preserving filters only when non-default.

    Args:
        request: Current HTTP request.
        page: Current page number; omitted from query when ``"1"``.
        per_page: Rows per page; omitted when equal to the default.
        search: Optional name filter; omitted when empty.
        anchor: Optional fragment identifier (without ``#``) to append for row highlighting.

    Returns:
        Fully qualified collection list URL, optionally with ``#anchor``.
    """
    params: dict[str, str] = {}
    if page and page != "1":
        params["page"] = page
    if per_page and per_page != str(_DEFAULT_PER_PAGE):
        params["per_page"] = per_page
    if search and search.strip():
        params["search"] = search.strip()
    base_url = str(request.url_for("arena_admin_collection_list"))
    url = f"{base_url}?{urlencode(params)}" if params else base_url
    return f"{url}#{anchor}" if anchor else url


async def _get_collection_or_404(collection_id: str, session: AsyncSession) -> ArenaCollection:
    """Fetch an Arena collection by id, raising HTTP 404 if not found."""
    collection = await admin_collection_service.get_collection(session, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return collection


def _effective_per_page(value: str | None) -> int:
    """Return an allowed page size for collection list views."""
    try:
        effective = int(value) if value else _DEFAULT_PER_PAGE
    except TypeError, ValueError:
        return _DEFAULT_PER_PAGE
    return effective if effective in _ALLOWED_PER_PAGE else _DEFAULT_PER_PAGE


@router.get("/collections", response_class=HTMLResponse, name="arena_admin_collection_list")
async def admin_collection_list(
    request: Request,
    flash: FlashDep,
    page: str | None = None,
    per_page: str | None = None,
    sort_by: str | None = None,
    search: str | None = None,
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the paginated Arena collection list."""
    effective_per_page = _effective_per_page(per_page)
    effective_sort = (
        sort_by if sort_by in admin_collection_service.VALID_SORTS else admin_collection_service.DEFAULT_SORT
    )
    effective_search = search.strip() if search and search.strip() else ""
    pagination = await admin_collection_service.list_collections_paginated(
        session,
        page=parse_page(page),
        per_page=effective_per_page,
        sort_by=effective_sort,
        search=effective_search or None,
    )
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/collection_list.html",
            {
                "pagination": pagination,
                "per_page": effective_per_page,
                "sort_by": effective_sort,
                "search": effective_search,
                "current_user": admin,
            },
        )
    )


@router.get("/collections/new", response_class=HTMLResponse, name="arena_admin_collection_new")
async def admin_collection_new(
    request: Request,
    flash: FlashDep,
    admin: ArenaUser = Depends(require_arena_admin),
) -> Response:
    """Render the create-collection form."""
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/collection_form.html",
            {
                "mode": "create",
                "collection": None,
                "form": {"name": "", "slug": ""},
                "back_url": str(request.url_for("arena_admin_collection_list")),
                "current_user": admin,
            },
        )
    )


@router.post("/collections/new", name="arena_admin_collection_create")
async def admin_collection_create(
    request: Request,
    flash: FlashDep,
    name: str = Form(""),
    slug: str = Form(""),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Create an Arena collection from submitted form data."""
    list_url = str(request.url_for("arena_admin_collection_list"))
    try:
        collection = await admin_collection_service.create_collection(session, name=name, slug=slug)
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(url=list_url, status_code=303)
    await session.commit()
    flash(f"Collection {collection.name} created.", FlashCategory.SUCCESS)
    return RedirectResponse(
        url=_collection_list_url(request, anchor=collection.id),
        status_code=303,
    )


@router.get("/collections/{collection_id}/edit", response_class=HTMLResponse, name="arena_admin_collection_edit")
async def admin_collection_edit(
    request: Request,
    collection_id: str,
    flash: FlashDep,
    page: str = "1",
    per_page: str = "25",
    search: str = "",
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the edit-collection form."""
    collection = await _get_collection_or_404(collection_id, session)
    problem_count = await admin_collection_service.get_problem_count(session, collection.id)
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/collection_form.html",
            {
                "mode": "edit",
                "collection": collection,
                "problem_count": problem_count,
                "form": {"name": collection.name, "slug": collection.slug},
                "back_url": _collection_list_url(request, page=page, per_page=per_page, search=search),
                "page": page,
                "per_page": per_page,
                "search": search,
                "current_user": admin,
            },
        )
    )


@router.post("/collections/{collection_id}/edit", name="arena_admin_collection_update")
async def admin_collection_update(
    request: Request,
    collection_id: str,
    flash: FlashDep,
    name: str = Form(""),
    slug: str = Form(""),
    page: str = Form("1"),
    per_page: str = Form("25"),
    search: str = Form(""),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Update an Arena collection from submitted form data."""
    collection = await _get_collection_or_404(collection_id, session)
    list_url = _collection_list_url(request, page=page, per_page=per_page, search=search)
    try:
        await admin_collection_service.update_collection(session, collection, name=name, slug=slug)
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(url=list_url, status_code=303)
    await session.commit()
    flash(f"Collection {collection.name} updated.", FlashCategory.SUCCESS)
    return RedirectResponse(
        url=_collection_list_url(request, page=page, per_page=per_page, search=search, anchor=collection_id),
        status_code=303,
    )


@router.post("/collections/{collection_id}/delete", name="arena_admin_collection_delete")
async def admin_collection_delete(
    request: Request,
    collection_id: str,
    flash: FlashDep,
    page: str = Form("1"),
    per_page: str = Form("25"),
    search: str = Form(""),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Delete an Arena collection and return to the collection list."""
    collection = await _get_collection_or_404(collection_id, session)
    collection_name = collection.name
    await admin_collection_service.delete_collection(session, collection)
    await record_admin_action(
        session,
        request,
        module="arena",
        actor_user_id=admin.id,
        actor_label=admin.email_normalizado,
        action="delete",
        target_type="arena_collection",
        target_id=collection_id,
        detail=f"name={collection_name}",
    )
    await session.commit()
    flash(f"Collection {collection_name} removed.", FlashCategory.SUCCESS)
    return RedirectResponse(
        url=_collection_list_url(request, page=page, per_page=per_page, search=search), status_code=303
    )
