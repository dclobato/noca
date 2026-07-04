#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi_flash import FlashCategory, FlashDep

from shared.services.admin_audit import record_admin_action
from web.dependencies import get_uberadmin
from web.models.users import UberAdmin
from web.services.uberadmin_service import (
    get_uberadmin_by_id,
    list_uberadmins,
    toggle_uberadmin_status,
    update_uberadmin,
)

router = APIRouter(prefix="/uberadmin", tags=["uberadmin"])


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[no-any-return]


@router.get("/uberadmins", response_class=HTMLResponse, name="list_uberadmins_route")
async def list_uberadmins_route(
    request: Request,
    q: str = Query(default=""),
    current_user: UberAdmin = Depends(get_uberadmin),
) -> HTMLResponse:
    """Render the searchable UberAdmin account list."""
    query = q.strip()
    async with request.app.state.db_session() as session:
        uberadmins = await list_uberadmins(session, query=query or None)

    return _templates(request).TemplateResponse(
        request,
        "uberadmin/list_uberadmins.html",
        {
            "current_user": current_user,
            "uberadmins": uberadmins,
            "q": query,
        },
    )


@router.get("/uberadmins/{uberadmin_id}/edit", response_class=HTMLResponse, name="edit_uberadmin_form")
async def edit_uberadmin_form(
    request: Request,
    uberadmin_id: str,
    current_user: UberAdmin = Depends(get_uberadmin),
) -> HTMLResponse:
    """Render the UberAdmin edit form."""
    async with request.app.state.db_session() as session:
        edit_ua = await get_uberadmin_by_id(session, uberadmin_id)
        if edit_ua is None:
            raise HTTPException(status_code=404)

        return _templates(request).TemplateResponse(
            request,
            "uberadmin/edit_uberadmin.html",
            {
                "current_user": current_user,
                "edit_ua": edit_ua,
                "error": "",
            },
        )


@router.post("/uberadmins/{uberadmin_id}/edit", response_model=None, name="edit_uberadmin_submit")
async def edit_uberadmin_submit(
    request: Request,
    uberadmin_id: str,
    flash: FlashDep,
    fullname: str = Form(""),
    email: str = Form(""),
    new_password: str = Form(""),
    current_user: UberAdmin = Depends(get_uberadmin),
) -> Response:
    """Persist changes from the UberAdmin edit form."""
    async with request.app.state.db_session() as session:
        result = await update_uberadmin(
            session,
            uberadmin_id=uberadmin_id,
            fullname=fullname,
            email=email,
            new_password=new_password,
        )
        if not result.success:
            edit_ua = await get_uberadmin_by_id(session, uberadmin_id)
            if edit_ua is None:
                raise HTTPException(status_code=404)
            return _templates(request).TemplateResponse(
                request,
                "uberadmin/edit_uberadmin.html",
                {
                    "current_user": current_user,
                    "edit_ua": edit_ua,
                    "error": result.error,
                },
                status_code=422,
            )

    flash("UberAdmin updated successfully.", FlashCategory.SUCCESS)
    if result.changed_password:
        flash(f"New password: {result.changed_password}", FlashCategory.INFO)
    return RedirectResponse(url=str(request.url_for("list_uberadmins_route")), status_code=303)


@router.post("/uberadmins/{uberadmin_id}/toggle", response_model=None, name="toggle_uberadmin_route")
async def toggle_uberadmin_route(
    request: Request,
    uberadmin_id: str,
    flash: FlashDep,
    current_user: UberAdmin = Depends(get_uberadmin),
) -> RedirectResponse:
    """Enable or disable an UberAdmin account."""
    redirect_url = str(request.url_for("list_uberadmins_route"))
    async with request.app.state.db_session() as session:
        try:
            updated = await toggle_uberadmin_status(
                session,
                uberadmin_id=uberadmin_id,
                actor_id=current_user.id,
            )
        except ValueError as exc:
            flash(str(exc), FlashCategory.WARNING)
            return RedirectResponse(url=redirect_url, status_code=303)
        if updated is not None:
            await record_admin_action(
                session,
                request,
                module="web",
                actor_user_id=current_user.id,
                action="enable" if updated.is_enabled else "disable",
                target_type="uberadmin",
                target_id=uberadmin_id,
            )
            await session.commit()

    if updated is None:
        flash("UberAdmin not found.", FlashCategory.WARNING)
        return RedirectResponse(url=redirect_url, status_code=303)

    if updated.is_enabled:
        flash("Account enabled.", FlashCategory.SUCCESS)
    else:
        flash("Account disabled.", FlashCategory.WARNING)
    return RedirectResponse(url=redirect_url, status_code=303)
