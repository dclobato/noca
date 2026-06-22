#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public Arena user image routes and current-user profile page."""

from typing import Any, cast

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_affiliations import ArenaAffiliation
from arena.models.arena_users import ArenaUser
from arena.routes.user_profile_api import router as profile_api_router
from arena.services import admin_user_service, backup2fa_service
from arena.services.arena_favorite_service import FavoriteProblemRow, get_favorites_paginated
from arena.services.pagination_service import Pagination, build_pagination_params, clamp_page
from arena.services.profile_location_service import list_countries
from arena.services.session_service import (
    build_current_next_url,
    build_login_redirect_response,
    missing_profile_fields,
)
from arena.services.submission_list_service import (
    ARENA_SUBMISSIONS_PER_PAGE,
    SubmissionListRow,
    get_user_submissions,
)
from arena.services.user_progress_service import (
    UserProgress,
    UserProgressSummary,
    get_attempted_progress,
    get_solved_progress,
    make_progress_summary,
)
from shared.db_schema import languages as languages_table
from shared.enumerations import ARENA_NOTIFICATION_ICONS, Verdict
from shared.services.arena_notification_service import (
    count_unread_arena_notifications,
    delete_all_arena_notifications,
    delete_arena_notification,
    mark_all_arena_notifications_read,
    paginate_arena_notifications,
)
from shared.services.imageprocessing_service import ImageProcessingError, ImageProcessingService

router = APIRouter(tags=["arena-users"])
router.include_router(profile_api_router)


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction.

    Args:
        response: Template response returned by Jinja2.

    Returns:
        HTMLResponse: Response typed for FastAPI handlers.
    """
    return cast(HTMLResponse, response)


async def _get_user_or_404(user_id: str, session: AsyncSession) -> ArenaUser:
    """Fetch an Arena user by id or raise 404.

    Args:
        user_id: Arena user id.
        session: Active database session.

    Returns:
        ArenaUser: Matching user.

    Raises:
        HTTPException: If no Arena user exists for the id.
    """
    user = await session.get(ArenaUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _build_image_response(request: Request, data: bytes, mime_type: str) -> Response:
    """Build an image response through the shared image processing service.

    Args:
        request: Current request with application state.
        data: Raw image bytes.
        mime_type: Image MIME type.

    Returns:
        Response: FastAPI response containing image bytes.
    """
    image_service: ImageProcessingService = request.app.state.image_service
    return image_service.build_image_response(data, mime_type)


@router.get("/user/{user_id}/photo", name="arena_user_photo_by_id")
async def arena_user_photo_by_id(
    user_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Return the stored full-size Arena user photo or generated fallback."""
    user = await _get_user_or_404(user_id, session)
    data, mime_type = user.foto
    return _build_image_response(request, data, mime_type)


_NOTIFICATIONS_PER_PAGE = 25
_SUBMISSIONS_PER_PAGE = ARENA_SUBMISSIONS_PER_PAGE
_CREDITS_PER_PAGE = 25


@router.get(
    "/user/profile/complete",
    response_class=HTMLResponse,
    name="arena_user_profile_completion",
)
async def arena_user_profile_completion(
    request: Request,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
) -> Response:
    """Prompt an authenticated user to complete required profile information."""
    if current_user is None:
        return build_login_redirect_response(request, next_url=build_current_next_url(request))

    missing_fields = missing_profile_fields(current_user)
    if not missing_fields:
        return RedirectResponse(url=str(request.url_for("arena_dashboard")), status_code=303)

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "users/profile_completion.html",
            {"missing_fields": missing_fields},
        )
    )


@router.get("/user/profile", response_class=HTMLResponse, name="arena_user_profile")
async def arena_user_profile(
    request: Request,
    tab: str | None = None,
    solved_page: str | None = None,
    attempted_page: str | None = None,
    favorite_page: str | None = None,
    notifications_page: str | None = None,
    submissions_page: str | None = None,
    submissions_search: str | None = None,
    submissions_verdict: str | None = None,
    credits_page: str | None = None,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the current Arena user's profile page.

    Args:
        request: Current HTTP request.
        tab: Requested active tab. Defaults to personal-security.
        solved_page: Requested solved-problems page.
        attempted_page: Requested attempted-problems page.
        favorite_page: Requested favorites page.
        notifications_page: Requested notifications page.
        submissions_page: Requested submissions history page.
        submissions_search: Optional search string for submission filtering.
        submissions_verdict: Optional verdict value for submission filtering.
        credits_page: Requested AI credit transactions page.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.

    Returns:
        HTMLResponse | RedirectResponse: Profile page for authenticated users,
        or a login redirect for guests.
    """
    if current_user is None:
        return build_login_redirect_response(request, next_url=build_current_next_url(request))

    # ── Determine active tab before any data fetching ──────────────────────
    _tab_aliases = {"personal": "personal-security", "security": "personal-security"}
    _valid_tabs = {
        "personal-security",
        "solved",
        "attempted",
        "favorites",
        "notifications",
        "submissions",
        "credits",
    }
    canonical = _tab_aliases.get(tab or "", tab or "")
    active_tab = canonical if canonical in _valid_tabs else "personal-security"
    if solved_page is not None:
        active_tab = "solved"
    if attempted_page is not None:
        active_tab = "attempted"
    if favorite_page is not None:
        active_tab = "favorites"
    if notifications_page is not None:
        active_tab = "notifications"
    if submissions_page is not None or submissions_search is not None or submissions_verdict is not None:
        active_tab = "submissions"
    if credits_page is not None:
        active_tab = "credits"

    # ── Always-needed: unread notification count (drives tab button badge) ─
    notifications_unread_count = await count_unread_arena_notifications(session, user_id=current_user.id)

    # ── Tab-specific data: only fetch what the active tab needs ────────────
    _empty_progress: Pagination[object] = Pagination(items=[], page=1, per_page=50, total=0)
    backup_code_count: int = 0
    active_languages: list[object] = []
    progress: UserProgress | None = None
    favorites: Pagination[FavoriteProblemRow] | None = None
    notifications: Pagination[object] | None = None
    submissions: Pagination[SubmissionListRow] | None = None
    credit_transactions = None

    if active_tab == "personal-security":
        if current_user.usa_2fa:
            backup_code_count = await backup2fa_service.contar_tokens_disponiveis(current_user, session)
        lang_result = await session.execute(
            select(languages_table)
            .where(languages_table.c.active == True)  # noqa: E712
            .order_by(languages_table.c.name)
        )
        active_languages = list(lang_result.mappings().all())
        progress = UserProgress(
            summary=make_progress_summary(current_user),
            solved=_empty_progress,  # type: ignore[arg-type]
            attempted=_empty_progress,  # type: ignore[arg-type]
        )

    elif active_tab == "solved":
        solved_page_data = await get_solved_progress(
            session=session,
            user=current_user,
            params=build_pagination_params(solved_page, per_page=50),
        )
        progress = UserProgress(
            summary=make_progress_summary(current_user),
            solved=solved_page_data,
            attempted=_empty_progress,  # type: ignore[arg-type]
        )

    elif active_tab == "attempted":
        attempted_page_data = await get_attempted_progress(
            session=session,
            user=current_user,
            params=build_pagination_params(attempted_page, per_page=50),
        )
        progress = UserProgress(
            summary=UserProgressSummary(
                solved_total=current_user.solved_problems or 0,
                user_rating=current_user.user_rating or 0,
            ),
            solved=_empty_progress,  # type: ignore[arg-type]
            attempted=attempted_page_data,
        )

    elif active_tab == "favorites":
        favorites = await get_favorites_paginated(
            session,
            user_id=current_user.id,
            params=build_pagination_params(favorite_page, per_page=50),
        )

    elif active_tab == "notifications":
        raw_notif_page = build_pagination_params(notifications_page, per_page=_NOTIFICATIONS_PER_PAGE)
        notif_rows, notif_total = await paginate_arena_notifications(
            session,
            user_id=current_user.id,
            page=raw_notif_page.page,
            per_page=_NOTIFICATIONS_PER_PAGE,
        )
        effective_notif_page = clamp_page(
            raw_notif_page.page,
            total=notif_total,
            per_page=_NOTIFICATIONS_PER_PAGE,
        )
        notifications = Pagination(
            items=notif_rows,
            page=effective_notif_page,
            per_page=_NOTIFICATIONS_PER_PAGE,
            total=notif_total,
        )

    elif active_tab == "submissions":
        submissions = await get_user_submissions(
            session=session,
            user_id=current_user.id,
            search=submissions_search,
            verdict_filter=submissions_verdict,
            params=build_pagination_params(submissions_page, per_page=_SUBMISSIONS_PER_PAGE),
        )

    elif active_tab == "credits":
        credit_transactions = await admin_user_service.get_credit_transactions_paginated(
            session,
            user_id=current_user.id,
            params=build_pagination_params(credits_page, per_page=_CREDITS_PER_PAGE),
        )

    sem_afiliacao = await session.scalar(select(ArenaAffiliation).where(ArenaAffiliation.name == "Sem afiliação"))
    sem_afiliacao_id = sem_afiliacao.id if sem_afiliacao else None

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "users/profile.html",
            {
                "current_user": current_user,
                "sem_afiliacao_id": sem_afiliacao_id,
                "backup_code_count": backup_code_count,
                "progress": progress,
                "favorites": favorites,
                "active_tab": active_tab,
                "countries": list_countries(),
                "active_languages": active_languages,
                "notifications": notifications,
                "notifications_unread_count": notifications_unread_count,
                "notification_icons": ARENA_NOTIFICATION_ICONS,
                "submissions": submissions,
                "submissions_search": submissions_search or "",
                "submissions_verdict": submissions_verdict or "",
                "all_verdicts": list(Verdict),
                "credit_transactions": credit_transactions,
            },
        )
    )


@router.post(
    "/user/profile/notifications/{notification_id}/delete",
    name="arena_user_profile_notification_delete",
)
async def arena_user_profile_notification_delete(
    notification_id: str,
    request: Request,
    flash: FlashDep,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Delete one notification belonging to the current Arena user.

    Args:
        notification_id: Id of the notification to delete.
        request: Current HTTP request.
        flash: Flash message dependency.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.

    Returns:
        RedirectResponse: Redirects to the notifications tab of the profile page.
    """
    if current_user is None:
        profile_url = request.url_for("arena_user_profile").path + "?tab=notifications"
        return build_login_redirect_response(request, next_url=profile_url)

    found = await delete_arena_notification(
        session,
        notification_id=notification_id,
        user_id=current_user.id,
    )
    await session.commit()

    if not found:
        flash("Notification not found.", FlashCategory.WARNING)
    else:
        flash("Notification removed.", FlashCategory.SUCCESS)

    profile_url = str(request.url_for("arena_user_profile"))
    return RedirectResponse(url=f"{profile_url}?tab=notifications", status_code=303)


@router.post(
    "/user/profile/notifications/delete-all",
    name="arena_user_profile_notifications_delete_all",
)
async def arena_user_profile_notifications_delete_all(
    request: Request,
    flash: FlashDep,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Delete all notifications belonging to the current Arena user.

    Args:
        request: Current HTTP request.
        flash: Flash message dependency.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.

    Returns:
        RedirectResponse: Redirects to the notifications tab of the profile page.
    """
    if current_user is None:
        profile_url = request.url_for("arena_user_profile").path + "?tab=notifications"
        return build_login_redirect_response(request, next_url=profile_url)

    count = await delete_all_arena_notifications(session, user_id=current_user.id)
    await session.commit()

    if count:
        flash(f"{count} notification{'s' if count != 1 else ''} removed.", FlashCategory.SUCCESS)
    else:
        flash("No notifications to remove.", FlashCategory.INFO)

    profile_url = str(request.url_for("arena_user_profile"))
    return RedirectResponse(url=f"{profile_url}?tab=notifications", status_code=303)


@router.post(
    "/user/profile/notifications/mark-all-read",
    name="arena_user_profile_notifications_mark_all_read",
)
async def arena_user_profile_notifications_mark_all_read(
    request: Request,
    flash: FlashDep,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Mark all unread notifications as read for the current Arena user.

    Args:
        request: Current HTTP request.
        flash: Flash message dependency.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.

    Returns:
        RedirectResponse: Redirects to the notifications tab of the profile page.
    """
    if current_user is None:
        profile_url = request.url_for("arena_user_profile").path + "?tab=notifications"
        return build_login_redirect_response(request, next_url=profile_url)

    count = await mark_all_arena_notifications_read(session, user_id=current_user.id)
    await session.commit()

    if count:
        flash(f"{count} notification{'s' if count != 1 else ''} marked as read.", FlashCategory.SUCCESS)
    else:
        flash("No unread notifications.", FlashCategory.INFO)

    profile_url = str(request.url_for("arena_user_profile"))
    return RedirectResponse(url=f"{profile_url}?tab=notifications", status_code=303)


@router.get("/user/{user_id}/avatar", name="arena_user_avatar_by_id")
async def arena_user_avatar_by_id(
    user_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Return the stored resized Arena user avatar or generated fallback."""
    user = await _get_user_or_404(user_id, session)
    data, mime_type = user.avatar
    return _build_image_response(request, data, mime_type)


@router.post("/user/profile/photo", name="arena_user_profile_photo_update")
async def arena_user_profile_photo_update(
    request: Request,
    flash: FlashDep,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
    foto_cropada: UploadFile | None = File(None),
) -> Response:
    """Update the current Arena user's profile photo from a pre-cropped upload.

    Args:
        request: Current HTTP request.
        flash: Flash message dependency.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.
        foto_cropada: Cropped image file produced by the client-side crop modal.

    Returns:
        RedirectResponse: Redirects to the profile page with a flash message.
    """
    if current_user is None:
        return build_login_redirect_response(request, next_url=request.url_for("arena_user_profile").path)

    if foto_cropada is None or not foto_cropada.filename:
        flash("No photo was received. Please select and crop an image.", FlashCategory.DANGER)
        return RedirectResponse(url=str(request.url_for("arena_user_profile")), status_code=303)

    image_service: ImageProcessingService = request.app.state.image_service
    try:
        processed = await image_service.process_upload_image(
            upload=foto_cropada,
            crop_aspect_ratio=True,
            aspect_width=2,
            aspect_height=3,
        )
    except (ImageProcessingError, ValueError) as exc:
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(url=str(request.url_for("arena_user_profile")), status_code=303)

    current_user.apply_processed_photo(
        foto_base64=processed.imagem_base64,
        avatar_base64=processed.avatar_base64,
        mime_type=processed.mime_type,
    )
    await session.commit()

    flash("Profile photo updated.", FlashCategory.SUCCESS)
    return RedirectResponse(url=str(request.url_for("arena_user_profile")), status_code=303)
