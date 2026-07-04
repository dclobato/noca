#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep

from shared.services.admin_audit import record_admin_action
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.routes.contest_admin_user_helpers import _credentials_payload, _html, _render_download_json
from web.services.assorted_utils import slugfy
from web.services.contest_user_service import (
    build_user_export_row,
    ensure_user_edit_allowed,
    get_user_in_contest,
    list_contest_sites_for_form,
    list_users_for_export,
    remove_user,
    update_user,
    validate_edit_user_form,
)
from web.services.profile_service import validate_fullname, validate_new_password

router = APIRouter(prefix="/c/{slug}/admin/users", tags=["contest_admin_users"])


@router.get("/export.json")
async def export_users(
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> Response:
    users = await list_users_for_export(ctx.session, ctx.contest)
    content = _credentials_payload(
        ctx.contest.login_slug,
        [build_user_export_row(user) for user in users],
    )
    safe_slug = slugfy(ctx.contest.login_slug)
    return _render_download_json(content, f"noca-users-{safe_slug}.json")


@router.get("/{user_id}/edit", response_class=HTMLResponse)
async def edit_user_form(
    request: Request,
    user_id: str,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse:
    templates = request.app.state.templates
    edit_user_obj = await get_user_in_contest(ctx.session, ctx.contest, user_id)
    if edit_user_obj is None:
        raise HTTPException(status_code=404)
    ensure_user_edit_allowed(ctx.actor, edit_user_obj)
    sites = await list_contest_sites_for_form(ctx.session, ctx.contest)

    return _html(
        templates.TemplateResponse(
            request,
            "admin/users/edit.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "edit_user": edit_user_obj,
                "can_remove_photo": ctx.actor.id != edit_user_obj.id,
                "is_locked": ctx.contest.is_past,
                "sites": sites,
            },
        )
    )


@router.post("/{user_id}/edit", response_model=None)
async def edit_user_submit(
    request: Request,
    user_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    fullname: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    site_id: str = Form(""),
    location: str = Form(""),
) -> Response:
    cleaned_fullname, normalized_email, errors = validate_edit_user_form(fullname, email)
    submitted_site_id = site_id.strip() or None
    submitted_location = location.strip()[:16] or None
    edit_user_obj = await get_user_in_contest(ctx.session, ctx.contest, user_id)
    if edit_user_obj is None:
        raise HTTPException(status_code=404)
    ensure_user_edit_allowed(ctx.actor, edit_user_obj)
    redirect_url = f"/c/{ctx.contest.login_slug}/admin/users/{user_id}/edit"

    if errors:
        for error in errors:
            flash(error, FlashCategory.WARNING)
        return RedirectResponse(url=redirect_url, status_code=303)

    try:
        raw_password = (password or "").strip() or None
        if raw_password:
            error_msg = validate_new_password(raw_password)
            if error_msg:
                flash(error_msg, FlashCategory.WARNING)
                return RedirectResponse(url=redirect_url, status_code=303)
        actual_password = await update_user(
            ctx.session,
            ctx.contest,
            edit_user_obj,
            fullname=validate_fullname(cleaned_fullname),
            role=edit_user_obj.role,
            password=raw_password,
            email=normalized_email,
            site_id=submitted_site_id,
            location=submitted_location,
        )
    except HTTPException as exc:
        flash(str(exc.detail), FlashCategory.WARNING)
        return RedirectResponse(url=redirect_url, status_code=303)
    except ValueError as exc:
        await ctx.session.rollback()
        flash(str(exc), FlashCategory.WARNING)
        return RedirectResponse(url=redirect_url, status_code=303)
    except Exception:  # noqa: BLE001
        await ctx.session.rollback()
        flash("Could not update user. Please review the submitted data and try again.", FlashCategory.WARNING)
        return RedirectResponse(url=redirect_url, status_code=303)

    flash("User updated successfully.", FlashCategory.SUCCESS)
    if actual_password is not None:
        flash(f"Password changed. New password: {actual_password}", FlashCategory.INFO)
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/{user_id}/remove")
async def remove_user_route(
    request: Request,
    user_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> RedirectResponse:
    user_obj = await get_user_in_contest(ctx.session, ctx.contest, user_id)
    if user_obj is None:
        raise HTTPException(status_code=404)

    await remove_user(ctx.session, ctx.contest, user_obj)
    await record_admin_action(
        ctx.session,
        request,
        module="web",
        actor_user_id=ctx.actor.id,
        action="delete",
        target_type="contest_user",
        target_id=user_id,
        detail=f"contest={ctx.contest.login_slug}",
    )
    await ctx.session.commit()
    flash("User removed from contest.", FlashCategory.SUCCESS)
    return RedirectResponse(url=f"/c/{ctx.contest.login_slug}/admin/users", status_code=303)
