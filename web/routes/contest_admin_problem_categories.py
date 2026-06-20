#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from web.database import get_db
from web.dependencies import get_avatar_viewer, get_uberadmin
from web.models import UberAdmin, User
from web.routes.contest_admin_problem_helpers import _html, _redirect
from web.services.category_service import (
    CategoryInUseError,
    count_problems_for_category,
    create_category,
    delete_category,
    get_category,
    list_categories,
    rename_category,
)

router = APIRouter(prefix="/categories", tags=["categories"])


def _require_admin_or_uberadmin(actor: UberAdmin | User, is_uberadmin: bool) -> None:
    if not is_uberadmin and actor.role != RoleEnum.ADMIN:
        raise HTTPException(status_code=403)


# ---------------------------------------------------------------------------
# Autocomplete (used by problem edit form)
# ---------------------------------------------------------------------------


@router.get("/autocomplete", name="problem_categories_autocomplete")
async def problem_categories_autocomplete(
    request: Request,
    q: str = Query(default=""),
    viewer: tuple[UberAdmin | User, bool] = Depends(get_avatar_viewer),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    cats = await list_categories(session, query=q or None, limit=10)
    return JSONResponse({"categories": [{"id": c.id, "name": c.name} for c in cats]})


# ---------------------------------------------------------------------------
# Browse
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse, name="manage_categories")
async def manage_categories(
    request: Request,
    viewer: tuple[UberAdmin | User, bool] = Depends(get_avatar_viewer),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    actor, is_uberadmin = viewer
    _require_admin_or_uberadmin(actor, is_uberadmin)
    templates = request.app.state.templates
    categories = await list_categories(session)
    return _html(
        templates.TemplateResponse(
            request,
            "admin/problems/categories_list.html",
            {
                "current_user": actor,
                "categories": categories,
                "is_uberadmin": is_uberadmin,
            },
        )
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.get("/new", response_class=HTMLResponse, name="new_category_form")
async def new_category_form(
    request: Request,
    viewer: tuple[UberAdmin | User, bool] = Depends(get_avatar_viewer),
) -> HTMLResponse:
    actor, is_uberadmin = viewer
    _require_admin_or_uberadmin(actor, is_uberadmin)
    templates = request.app.state.templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/problems/category_edit.html",
            {
                "current_user": actor,
                "is_uberadmin": is_uberadmin,
                "category": None,
                "form_data": {},
            },
        )
    )


@router.post("/new", response_model=None, name="new_category_submit")
async def new_category_submit(
    request: Request,
    flash: FlashDep,
    viewer: tuple[UberAdmin | User, bool] = Depends(get_avatar_viewer),
    session: AsyncSession = Depends(get_db),
    name: str = Form(""),
) -> HTMLResponse | RedirectResponse:
    actor, is_uberadmin = viewer
    _require_admin_or_uberadmin(actor, is_uberadmin)
    try:
        cat = await create_category(session, name)
        await session.commit()
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return _redirect(str(request.url_for("new_category_form")))
    flash("Category created successfully.", FlashCategory.SUCCESS)
    return _redirect(str(request.url_for("manage_categories")) + f"#{cat.id}")


# ---------------------------------------------------------------------------
# Edit (rename)
# ---------------------------------------------------------------------------


@router.get("/{category_id}/edit", response_class=HTMLResponse, name="edit_category_form")
async def edit_category_form(
    request: Request,
    category_id: str,
    viewer: tuple[UberAdmin | User, bool] = Depends(get_avatar_viewer),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    actor, is_uberadmin = viewer
    _require_admin_or_uberadmin(actor, is_uberadmin)
    templates = request.app.state.templates
    category = await get_category(session, category_id)
    if category is None:
        return _redirect(str(request.url_for("manage_categories")))  # type: ignore[return-value]
    problem_count = await count_problems_for_category(session, category)
    return _html(
        templates.TemplateResponse(
            request,
            "admin/problems/category_edit.html",
            {
                "current_user": actor,
                "is_uberadmin": is_uberadmin,
                "category": category,
                "problem_count": problem_count,
                "form_data": {"name": category.name},
            },
        )
    )


@router.post("/{category_id}/edit", response_model=None, name="edit_category_submit")
async def edit_category_submit(
    request: Request,
    category_id: str,
    flash: FlashDep,
    viewer: tuple[UberAdmin | User, bool] = Depends(get_avatar_viewer),
    session: AsyncSession = Depends(get_db),
    name: str = Form(""),
) -> RedirectResponse:
    actor, is_uberadmin = viewer
    _require_admin_or_uberadmin(actor, is_uberadmin)
    category = await get_category(session, category_id)
    if category is None:
        flash("Category not found.", FlashCategory.DANGER)
        return _redirect(str(request.url_for("manage_categories")))
    try:
        await rename_category(session, category, name)
        await session.commit()
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return _redirect(str(request.url_for("edit_category_form", category_id=category_id)))
    flash("Category updated successfully.", FlashCategory.SUCCESS)
    return _redirect(str(request.url_for("manage_categories")) + f"#{category_id}")


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@router.post("/{category_id}/delete", response_model=None, name="delete_category")
async def delete_category_route(
    request: Request,
    category_id: str,
    flash: FlashDep,
    actor: UberAdmin = Depends(get_uberadmin),
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    list_url = str(request.url_for("manage_categories"))
    category = await get_category(session, category_id)
    if category is None:
        flash("Category not found.", FlashCategory.DANGER)
        return _redirect(list_url)
    try:
        await delete_category(session, category)
        await session.commit()
    except CategoryInUseError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return _redirect(list_url)
    flash("Category deleted successfully.", FlashCategory.SUCCESS)
    return _redirect(list_url)
