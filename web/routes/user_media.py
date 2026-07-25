#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest-user photo, avatar, and audio routes."""

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep

from shared.enumerations import RoleEnum
from shared.services.imageprocessing_service import ImageProcessingError
from web.config import settings
from web.dependencies import UserMediaContext, get_user_media_context, get_visible_user
from web.models.users import User
from web.routes.profile import _PROFILE_TEMPLATE, _profile_context, _resolve_user_site_name
from web.services.contest_service import get_contest_by_id
from web.services.contest_user_service import (
    ensure_contest_user_add_or_edit_allowed,
    ensure_user_media_removal_allowed,
    ensure_user_media_upload_allowed,
)
from web.services.user_media_service import (
    get_user_media,
    process_audio_upload,
    remove_audio,
    remove_photo,
    update_audio,
    update_photo,
)

router = APIRouter()

VisibleUserDep = Annotated[User, Depends(get_visible_user)]
UserMediaContextDep = Annotated[UserMediaContext, Depends(get_user_media_context)]
OptionalUploadDep = Annotated[UploadFile | None, File()]
ReturnToFormDep = Annotated[str, Form()]


def _html_response(response: Any) -> HTMLResponse:
    return cast(HTMLResponse, response)


def _admin_edit_redirect(contest_slug: str, user_id: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/c/{contest_slug}/admin/users/{user_id}/edit",
        status_code=303,
    )


@router.get("/user/{user_id}/avatar")
async def user_avatar_by_id(request: Request, user: VisibleUserDep) -> Response:
    """Serve a stored avatar or deterministic fallback."""
    async with request.app.state.db_session() as session:
        media = await get_user_media(session, user.id)
        stored_avatar = media.avatar if media is not None else None
    data, mime = stored_avatar or user.generated_avatar
    directive = "public" if stored_avatar is not None else "private"
    return cast(
        Response,
        request.app.state.image_service.build_image_response(data, mime, cache_directive=directive),
    )


@router.get("/user/{user_id}/photo")
async def user_photo_by_id(request: Request, user: VisibleUserDep) -> Response:
    """Serve a stored full photo or deterministic fallback."""
    async with request.app.state.db_session() as session:
        media = await get_user_media(session, user.id)
        stored_photo = media.foto if media is not None else None
    data, mime = stored_photo or user.generated_avatar
    directive = "public" if stored_photo is not None else "private"
    return cast(
        Response,
        request.app.state.image_service.build_image_response(data, mime, cache_directive=directive),
    )


@router.get("/user/{user_id}/audio")
async def user_audio_by_id(request: Request, user: VisibleUserDep) -> Response:
    """Serve a user's stored audio clip to an authorized viewer."""
    async with request.app.state.db_session() as session:
        media = await get_user_media(session, user.id)
        stored_audio = media.audio if media is not None else None
    if stored_audio is None:
        raise HTTPException(status_code=404, detail="Audio clip not found.")
    data, mime = stored_audio
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": f"public, max-age={settings.IMAGE_RESPONSE_CACHE_MAX_AGE}"},
    )


@router.post("/user/{user_id}/photo", response_class=HTMLResponse)
async def user_photo_submit(
    request: Request,
    user_id: str,
    flash: FlashDep,
    media_ctx: UserMediaContextDep,
    foto_cropada: OptionalUploadDep = None,
    return_to: ReturnToFormDep = "",
) -> Response:
    """Validate, crop, and store a contest-user photo."""
    templates = request.app.state.templates
    async with request.app.state.db_session() as session:
        user = await session.get(User, media_ctx.target_user.id)
        if user is None:
            raise RuntimeError("Target user disappeared during photo upload.")
        contest = await get_contest_by_id(session, user.contest_id)
        if contest is None:
            raise RuntimeError("Contest not found for target user.")
        ensure_user_media_upload_allowed(media_ctx.actor, user)
        if not media_ctx.is_self:
            ensure_contest_user_add_or_edit_allowed(contest)

        if foto_cropada is None:
            error = "Please confirm the crop before saving the photo."
            if return_to == "admin_edit":
                flash(error, FlashCategory.WARNING)
                return _admin_edit_redirect(contest.login_slug, user_id)
            site_name = await _resolve_user_site_name(session, user)
            user_media = await get_user_media(session, user.id)
            return _html_response(
                templates.TemplateResponse(
                    request,
                    _PROFILE_TEMPLATE,
                    _profile_context(
                        user,
                        contest=contest,
                        site_name=site_name,
                        user_media=user_media,
                        photo_error=error,
                    ),
                    status_code=422,
                )
            )

        aspect_width, aspect_height = (16, 10) if user.role == RoleEnum.TEAM else (2, 3)
        try:
            result = await request.app.state.image_service.process_upload_image(
                upload=foto_cropada,
                crop_aspect_ratio=True,
                aspect_width=aspect_width,
                aspect_height=aspect_height,
            )
        except (ImageProcessingError, ValueError) as exc:
            if return_to == "admin_edit":
                flash(str(exc), FlashCategory.WARNING)
                return _admin_edit_redirect(contest.login_slug, user_id)
            site_name = await _resolve_user_site_name(session, user)
            user_media = await get_user_media(session, user.id)
            return _html_response(
                templates.TemplateResponse(
                    request,
                    _PROFILE_TEMPLATE,
                    _profile_context(
                        user,
                        contest=contest,
                        site_name=site_name,
                        user_media=user_media,
                        photo_error=str(exc),
                    ),
                    status_code=422,
                )
            )
        await update_photo(session, user, result)

    flash("Photo updated successfully.", FlashCategory.SUCCESS)
    if return_to == "admin_edit":
        return _admin_edit_redirect(contest.login_slug, user_id)
    return RedirectResponse(url="/profile", status_code=303)


@router.post("/user/{user_id}/photo/remove", response_class=HTMLResponse)
async def user_photo_remove(
    request: Request,
    user_id: str,
    flash: FlashDep,
    media_ctx: UserMediaContextDep,
    return_to: ReturnToFormDep = "",
) -> Response:
    """Remove a contest user's stored photo."""
    async with request.app.state.db_session() as session:
        user = await session.get(User, media_ctx.target_user.id)
        if user is None:
            raise RuntimeError("Target user disappeared during photo removal.")
        contest = await get_contest_by_id(session, user.contest_id)
        if contest is None:
            raise RuntimeError("Contest not found for target user.")
        if not media_ctx.is_self:
            ensure_contest_user_add_or_edit_allowed(contest)
        ensure_user_media_removal_allowed(media_ctx.actor, user)
        await remove_photo(session, user)

    flash("Photo removed successfully.", FlashCategory.SUCCESS)
    if return_to == "admin_edit":
        return _admin_edit_redirect(contest.login_slug, user_id)
    return RedirectResponse(url="/profile", status_code=303)


@router.post("/user/{user_id}/audio", response_class=HTMLResponse)
async def user_audio_submit(
    request: Request,
    user_id: str,
    flash: FlashDep,
    media_ctx: UserMediaContextDep,
    audio_clip: OptionalUploadDep = None,
    return_to: ReturnToFormDep = "",
) -> Response:
    """Validate and store a contest user's audio clip."""
    templates = request.app.state.templates
    async with request.app.state.db_session() as session:
        user = await session.get(User, media_ctx.target_user.id)
        if user is None:
            raise RuntimeError("Target user disappeared during audio upload.")
        contest = await get_contest_by_id(session, user.contest_id)
        if contest is None:
            raise RuntimeError("Contest not found for target user.")
        ensure_user_media_upload_allowed(media_ctx.actor, user)
        if not media_ctx.is_self:
            ensure_contest_user_add_or_edit_allowed(contest)

        try:
            if audio_clip is None:
                raise ValueError("Please select an audio clip.")
            result = await process_audio_upload(
                audio_clip,
                max_file_size=settings.AUDIO_MAX_FILE_SIZE,
            )
        except ValueError as exc:
            if return_to == "admin_edit":
                flash(str(exc), FlashCategory.WARNING)
                return _admin_edit_redirect(contest.login_slug, user_id)
            site_name = await _resolve_user_site_name(session, user)
            user_media = await get_user_media(session, user.id)
            return _html_response(
                templates.TemplateResponse(
                    request,
                    _PROFILE_TEMPLATE,
                    _profile_context(
                        user,
                        contest=contest,
                        site_name=site_name,
                        user_media=user_media,
                        audio_error=str(exc),
                    ),
                    status_code=422,
                )
            )
        await update_audio(session, user, result)

    flash("Audio clip updated successfully.", FlashCategory.SUCCESS)
    if return_to == "admin_edit":
        return _admin_edit_redirect(contest.login_slug, user_id)
    return RedirectResponse(url="/profile", status_code=303)


@router.post("/user/{user_id}/audio/remove", response_class=HTMLResponse)
async def user_audio_remove(
    request: Request,
    user_id: str,
    flash: FlashDep,
    media_ctx: UserMediaContextDep,
    return_to: ReturnToFormDep = "",
) -> Response:
    """Remove a contest user's stored audio clip."""
    async with request.app.state.db_session() as session:
        user = await session.get(User, media_ctx.target_user.id)
        if user is None:
            raise RuntimeError("Target user disappeared during audio removal.")
        contest = await get_contest_by_id(session, user.contest_id)
        if contest is None:
            raise RuntimeError("Contest not found for target user.")
        if not media_ctx.is_self:
            ensure_contest_user_add_or_edit_allowed(contest)
        ensure_user_media_removal_allowed(media_ctx.actor, user)
        await remove_audio(session, user)

    flash("Audio clip removed successfully.", FlashCategory.SUCCESS)
    if return_to == "admin_edit":
        return _admin_edit_redirect(contest.login_slug, user_id)
    return RedirectResponse(url="/profile", status_code=303)
