#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Self-service profile routes: display name, password, and photo management."""

from typing import Any, cast

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy import select

from shared.enumerations import RoleEnum
from shared.services.imageprocessing_service import ImageProcessingError
from shared.services.password_service import PasswordPolicy
from web.config import settings
from web.dependencies import (
    UserPhotoContext,
    get_avatar_viewer,
    get_user_photo_context,
    get_visible_user,
)
from web.models.contest import Contest
from web.models.site import Site
from web.models.users import UberAdmin, User
from web.services.contest_service import get_contest_by_id
from web.services.contest_user_service import (
    ensure_contest_user_add_or_edit_allowed,
    ensure_user_photo_removal_allowed,
    ensure_user_photo_upload_allowed,
)
from web.services.profile_service import (
    remove_photo,
    update_email,
    update_fullname,
    update_password,
    update_photo,
)

router = APIRouter()

_PROFILE_TEMPLATE = "profile/user_profile.html"


_policy = PasswordPolicy(settings)


def _profile_context(user: UberAdmin | User, **overrides: object) -> dict[str, object]:
    return {
        "current_user": user,
        "contest": None,
        "is_uberadmin": isinstance(user, UberAdmin),
        "site_name": None,
        "fullname_value": getattr(user, "fullname", ""),
        "email_value": user.email or "",
        "fullname_error": None,
        "email_error": None,
        "password_error": None,
        "password_hint": _policy.policy_hint,
        "photo_error": None,
        **overrides,
    }


def _html_response(response: Any) -> HTMLResponse:
    return cast(HTMLResponse, response)


def _image_response(response: Any) -> Response:
    return cast(Response, response)


def _admin_edit_context(
    actor: object,
    contest: Contest,
    edit_user: User,
    *,
    errors: list[str] | None = None,
    photo_removed: bool = False,
) -> dict[str, object]:
    return {
        "current_user": actor,
        "contest": contest,
        "edit_user": edit_user,
        "can_remove_photo": getattr(actor, "id", None) != edit_user.id,
        "is_locked": contest.is_past,
        "success": False,
        "photo_removed": photo_removed,
        "new_password": None,
        "errors": errors or [],
    }


async def _resolve_profile_user_and_contest(request: Request) -> tuple[UberAdmin | User, Contest | None]:
    async with request.app.state.db_session() as session:
        actor, is_uberadmin = await get_avatar_viewer(request, session)
        if is_uberadmin:
            contest = None
        else:
            assert isinstance(actor, User)
            contest = await get_contest_by_id(session, actor.contest_id)
    return actor, contest


async def _resolve_user_site_name(session: Any, actor: UberAdmin | User) -> str | None:
    if isinstance(actor, UberAdmin) or actor.site_id is None:
        return None
    return cast(str | None, await session.scalar(select(Site.sitename).where(Site.id == actor.site_id)))


# ---------------------------------------------------------------------------
# User image serving
# ---------------------------------------------------------------------------


@router.get("/user/{user_id}/avatar")
async def user_avatar_by_id(request: Request, user: User = Depends(get_visible_user)) -> Response:
    data, mime = user.avatar
    image_service = request.app.state.image_service
    directive = "public" if user.com_foto else "private"
    return _image_response(image_service.build_image_response(data, mime, cache_directive=directive))


@router.get("/user/{user_id}/photo")
async def user_photo_by_id(request: Request, user: User = Depends(get_visible_user)) -> Response:
    data, mime = user.foto
    image_service = request.app.state.image_service
    directive = "public" if user.com_foto else "private"
    return _image_response(image_service.build_image_response(data, mime, cache_directive=directive))


# ---------------------------------------------------------------------------
# Profile page
# ---------------------------------------------------------------------------


@router.get("/profile", response_class=HTMLResponse)
async def profile_get(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    actor, contest = await _resolve_profile_user_and_contest(request)
    async with request.app.state.db_session() as session:
        site_name = await _resolve_user_site_name(session, actor)
    return _html_response(
        templates.TemplateResponse(
            request,
            _PROFILE_TEMPLATE,
            _profile_context(actor, contest=contest, site_name=site_name),
        )
    )


# ---------------------------------------------------------------------------
# Update display name
# ---------------------------------------------------------------------------


@router.post("/profile/fullname", response_class=HTMLResponse)
async def profile_fullname_submit(
    request: Request,
    flash: FlashDep,
    fullname: str = Form(""),
) -> Response:
    templates = request.app.state.templates
    actor, contest = await _resolve_profile_user_and_contest(request)
    async with request.app.state.db_session() as session:
        actor, _ = await get_avatar_viewer(request, session)
        is_admin = isinstance(actor, UberAdmin) or (isinstance(actor, User) and actor.role == RoleEnum.ADMIN)
        if not is_admin:
            raise HTTPException(status_code=403, detail="Only administrators may change their display name.")
        try:
            await update_fullname(session, actor, fullname)
        except ValueError as exc:
            site_name = await _resolve_user_site_name(session, actor)
            return _html_response(
                templates.TemplateResponse(
                    request,
                    _PROFILE_TEMPLATE,
                    _profile_context(
                        actor,
                        contest=contest,
                        site_name=site_name,
                        fullname_value=fullname,
                        fullname_error=str(exc),
                    ),
                    status_code=422,
                )
            )
    flash("Display name updated successfully.", FlashCategory.SUCCESS)
    return RedirectResponse(url="/profile", status_code=303)


@router.post("/profile/email", response_class=HTMLResponse)
async def profile_email_submit(
    request: Request,
    flash: FlashDep,
    email: str = Form(""),
) -> Response:
    templates = request.app.state.templates
    actor, contest = await _resolve_profile_user_and_contest(request)
    async with request.app.state.db_session() as session:
        actor, _ = await get_avatar_viewer(request, session)
        try:
            await update_email(session, actor, email)
        except ValueError as exc:
            site_name = await _resolve_user_site_name(session, actor)
            return _html_response(
                templates.TemplateResponse(
                    request,
                    _PROFILE_TEMPLATE,
                    _profile_context(
                        actor,
                        contest=contest,
                        site_name=site_name,
                        email_value=email,
                        email_error=str(exc),
                    ),
                    status_code=422,
                )
            )

    flash("Email updated successfully.", FlashCategory.SUCCESS)
    return RedirectResponse(url="/profile", status_code=303)


# ---------------------------------------------------------------------------
# Change password
# ---------------------------------------------------------------------------


@router.post("/profile/password", response_class=HTMLResponse)
async def profile_password_submit(
    request: Request,
    flash: FlashDep,
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
) -> Response:
    templates = request.app.state.templates

    if not new_password:
        return RedirectResponse(url="/profile", status_code=303)

    actor, contest = await _resolve_profile_user_and_contest(request)
    async with request.app.state.db_session() as session:
        actor, _ = await get_avatar_viewer(request, session)

        if new_password != confirm_password:
            site_name = await _resolve_user_site_name(session, actor)
            return _html_response(
                templates.TemplateResponse(
                    request,
                    _PROFILE_TEMPLATE,
                    _profile_context(
                        actor,
                        contest=contest,
                        site_name=site_name,
                        password_error="New passwords do not match.",
                    ),
                    status_code=422,
                )
            )

        error = await update_password(session, actor, new_password, current_password=current_password)
        if error:
            site_name = await _resolve_user_site_name(session, actor)
            return _html_response(
                templates.TemplateResponse(
                    request,
                    _PROFILE_TEMPLATE,
                    _profile_context(actor, contest=contest, site_name=site_name, password_error=error),
                    status_code=422,
                )
            )

    flash("Password changed successfully.", FlashCategory.SUCCESS)
    return RedirectResponse(url="/profile", status_code=303)


# ---------------------------------------------------------------------------
# Upload photo
# ---------------------------------------------------------------------------


@router.post("/user/{user_id}/photo", response_class=HTMLResponse)
async def user_photo_submit(
    request: Request,
    user_id: str,
    flash: FlashDep,
    photo_ctx: UserPhotoContext = Depends(get_user_photo_context),
    foto_cropada: UploadFile | None = File(None),
) -> Response:
    templates = request.app.state.templates
    async with request.app.state.db_session() as session:
        user = await session.get(User, photo_ctx.target_user.id)
        if user is None:
            raise RuntimeError("Target user disappeared during photo upload.")
        contest = await get_contest_by_id(session, user.contest_id)
        if contest is None:
            raise RuntimeError("Contest not found for authenticated user.")

        ensure_user_photo_upload_allowed(photo_ctx.actor, user)

        if foto_cropada is None:
            site_name = await _resolve_user_site_name(session, user)
            return _html_response(
                templates.TemplateResponse(
                    request,
                    _PROFILE_TEMPLATE,
                    _profile_context(
                        user,
                        contest=contest,
                        site_name=site_name,
                        photo_error="Please confirm the crop before saving the photo.",
                    ),
                    status_code=422,
                )
            )

        if user.role == RoleEnum.TEAM:
            aspect_width, aspect_height = 16, 10
        else:
            aspect_width, aspect_height = 2, 3

        image_service = request.app.state.image_service
        try:
            result = await image_service.process_upload_image(
                upload=foto_cropada,
                crop_aspect_ratio=True,
                aspect_width=aspect_width,
                aspect_height=aspect_height,
            )
        except (ImageProcessingError, ValueError) as exc:
            site_name = await _resolve_user_site_name(session, user)
            return _html_response(
                templates.TemplateResponse(
                    request,
                    _PROFILE_TEMPLATE,
                    _profile_context(user, contest=contest, site_name=site_name, photo_error=str(exc)),
                    status_code=422,
                )
            )

        await update_photo(session, user, result)

    flash("Photo updated successfully.", FlashCategory.SUCCESS)
    return RedirectResponse(url="/profile", status_code=303)


# ---------------------------------------------------------------------------
# Remove photo
# ---------------------------------------------------------------------------


@router.post("/user/{user_id}/photo/remove", response_class=HTMLResponse)
async def user_photo_remove(
    request: Request,
    user_id: str,
    flash: FlashDep,
    photo_ctx: UserPhotoContext = Depends(get_user_photo_context),
    return_to: str = Form(""),
) -> Response:
    templates = request.app.state.templates
    async with request.app.state.db_session() as session:
        user = await session.get(User, photo_ctx.target_user.id)
        if user is None:
            raise RuntimeError("Target user disappeared during photo removal.")
        contest = await get_contest_by_id(session, user.contest_id)
        if contest is None:
            raise RuntimeError("Contest not found for target user.")
        try:
            if not photo_ctx.is_self:
                ensure_contest_user_add_or_edit_allowed(contest)
            ensure_user_photo_removal_allowed(photo_ctx.actor, user)
            await remove_photo(session, user)
        except HTTPException as exc:
            if return_to == "admin_edit":
                return _html_response(
                    templates.TemplateResponse(
                        request,
                        "admin/users/edit.html",
                        _admin_edit_context(photo_ctx.actor, contest, user, errors=[str(exc.detail)]),
                        status_code=exc.status_code,
                    )
                )
            raise
        except Exception:  # noqa: BLE001
            if return_to == "admin_edit":
                await session.rollback()
                return _html_response(
                    templates.TemplateResponse(
                        request,
                        "admin/users/edit.html",
                        _admin_edit_context(
                            photo_ctx.actor,
                            contest,
                            user,
                            errors=["Could not remove photo. Please try again."],
                        ),
                        status_code=422,
                    )
                )
            raise

    if return_to == "admin_edit":
        flash("Photo removed successfully.", FlashCategory.SUCCESS)
        return RedirectResponse(
            url=f"/c/{contest.login_slug}/admin/users/{user_id}/edit",
            status_code=303,
        )

    flash("Photo removed successfully.", FlashCategory.SUCCESS)
    return RedirectResponse(url="/profile", status_code=303)
