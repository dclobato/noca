#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin category CRUD routes."""

import colorsys
import random
from typing import Any, cast
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.admin import require_arena_admin
from arena.models.arena_problems import ArenaCategory
from arena.models.arena_users import ArenaUser
from arena.services import admin_category_service
from arena.services.pagination_service import parse_page

router = APIRouter(prefix="/admin", tags=["arena-admin"])

_ALLOWED_PER_PAGE = [10, 25, 50, 100]
_DEFAULT_PER_PAGE = 25
_DEFAULT_COLOR = "#6c757d"


def _random_category_color() -> str:
    """Return a random, visually pleasing hex color for a new category.

    Generates a color in HSL space with a random hue and constrained
    saturation/lightness so the result is always vivid and readable as a
    badge background.

    Returns:
        A hex color string such as ``"#4a9ef2"``.
    """
    hue = random.random()
    saturation = random.uniform(0.55, 0.75)
    lightness = random.uniform(0.40, 0.58)
    red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
    return f"#{int(red * 255):02x}{int(green * 255):02x}{int(blue * 255):02x}"


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def _category_list_url(
    request: Request,
    *,
    page: str = "1",
    per_page: str = "25",
    search: str | None = None,
    anchor: str | None = None,
) -> str:
    """Build a category list URL preserving filters only when non-default.

    Args:
        request: Current HTTP request.
        page: Current page number; omitted from query when ``"1"``.
        per_page: Rows per page; omitted when equal to the default.
        search: Optional name filter; omitted when empty.
        anchor: Optional fragment identifier (without ``#``) to append for row highlighting.

    Returns:
        Fully qualified category list URL, optionally with ``#anchor``.
    """
    params: dict[str, str] = {}
    if page and page != "1":
        params["page"] = page
    if per_page and per_page != str(_DEFAULT_PER_PAGE):
        params["per_page"] = per_page
    if search and search.strip():
        params["search"] = search.strip()
    base_url = str(request.url_for("arena_admin_category_list"))
    url = f"{base_url}?{urlencode(params)}" if params else base_url
    return f"{url}#{anchor}" if anchor else url


async def _get_category_or_404(category_id: str, session: AsyncSession) -> ArenaCategory:
    """Fetch an Arena category by id, raising HTTP 404 if not found."""
    category = await admin_category_service.get_category(session, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    return category


def _effective_per_page(value: str | None) -> int:
    """Return an allowed page size for category list views."""
    try:
        effective = int(value) if value else _DEFAULT_PER_PAGE
    except TypeError, ValueError:
        return _DEFAULT_PER_PAGE
    return effective if effective in _ALLOWED_PER_PAGE else _DEFAULT_PER_PAGE


@router.get("/categories", response_class=HTMLResponse, name="arena_admin_category_list")
async def admin_category_list(
    request: Request,
    flash: FlashDep,
    page: str | None = None,
    per_page: str | None = None,
    sort_by: str | None = None,
    search: str | None = None,
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the paginated Arena category list."""
    effective_per_page = _effective_per_page(per_page)
    effective_sort = sort_by if sort_by in admin_category_service.VALID_SORTS else admin_category_service.DEFAULT_SORT
    effective_search = search.strip() if search and search.strip() else ""
    pagination = await admin_category_service.list_categories_paginated(
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
            "admin/category_list.html",
            {
                "pagination": pagination,
                "per_page": effective_per_page,
                "sort_by": effective_sort,
                "search": effective_search,
                "current_user": admin,
            },
        )
    )


@router.get("/categories/new", response_class=HTMLResponse, name="arena_admin_category_new")
async def admin_category_new(
    request: Request,
    flash: FlashDep,
    admin: ArenaUser = Depends(require_arena_admin),
) -> Response:
    """Render the create-category form."""
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/category_form.html",
            {
                "mode": "create",
                "category": None,
                "form": {"name": "", "slug": "", "color": _random_category_color()},
                "back_url": str(request.url_for("arena_admin_category_list")),
                "current_user": admin,
            },
        )
    )


@router.post("/categories/new", name="arena_admin_category_create")
async def admin_category_create(
    request: Request,
    flash: FlashDep,
    name: str = Form(""),
    slug: str = Form(""),
    color: str = Form(_DEFAULT_COLOR),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Create an Arena category from submitted form data."""
    list_url = str(request.url_for("arena_admin_category_list"))
    try:
        category = await admin_category_service.create_category(session, name=name, slug=slug, color=color)
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(url=list_url, status_code=303)
    await session.commit()
    flash(f"Category {category.name} created.", FlashCategory.SUCCESS)
    return RedirectResponse(
        url=_category_list_url(request, anchor=category.id),
        status_code=303,
    )


@router.get("/categories/{category_id}/edit", response_class=HTMLResponse, name="arena_admin_category_edit")
async def admin_category_edit(
    request: Request,
    category_id: str,
    flash: FlashDep,
    page: str = "1",
    per_page: str = "25",
    search: str = "",
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the edit-category form."""
    category = await _get_category_or_404(category_id, session)
    problem_count = await admin_category_service.get_problem_count(session, category.id)
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/category_form.html",
            {
                "mode": "edit",
                "category": category,
                "problem_count": problem_count,
                "form": {"name": category.name, "slug": category.slug, "color": category.color},
                "back_url": _category_list_url(request, page=page, per_page=per_page, search=search),
                "page": page,
                "per_page": per_page,
                "search": search,
                "current_user": admin,
            },
        )
    )


@router.post("/categories/{category_id}/edit", name="arena_admin_category_update")
async def admin_category_update(
    request: Request,
    category_id: str,
    flash: FlashDep,
    name: str = Form(""),
    slug: str = Form(""),
    color: str = Form(_DEFAULT_COLOR),
    page: str = Form("1"),
    per_page: str = Form("25"),
    search: str = Form(""),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Update an Arena category from submitted form data."""
    category = await _get_category_or_404(category_id, session)
    list_url = _category_list_url(request, page=page, per_page=per_page, search=search)
    try:
        await admin_category_service.update_category(session, category, name=name, slug=slug, color=color)
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(url=list_url, status_code=303)
    await session.commit()
    flash(f"Category {category.name} updated.", FlashCategory.SUCCESS)
    return RedirectResponse(
        url=_category_list_url(request, page=page, per_page=per_page, search=search, anchor=category_id),
        status_code=303,
    )


@router.post("/categories/{category_id}/delete", name="arena_admin_category_delete")
async def admin_category_delete(
    request: Request,
    category_id: str,
    flash: FlashDep,
    page: str = Form("1"),
    per_page: str = Form("25"),
    search: str = Form(""),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Delete an Arena category and return to the category list."""
    category = await _get_category_or_404(category_id, session)
    category_name = category.name
    await admin_category_service.delete_category(session, category)
    await session.commit()
    flash(f"Category {category_name} removed.", FlashCategory.SUCCESS)
    return RedirectResponse(
        url=_category_list_url(request, page=page, per_page=per_page, search=search), status_code=303
    )
