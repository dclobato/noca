#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Self-service profile routes for identity and password management."""

from typing import Any, cast

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy import select

from shared.enumerations import RoleEnum
from shared.services.password_service import PasswordPolicy
from web.config import settings
from web.dependencies import get_avatar_viewer
from web.models.contest import Contest
from web.models.site import Site
from web.models.users import UberAdmin, User
from web.services.contest_service import get_contest_by_id
from web.services.password_confirm_throttle import confirm_password as verify_current_password
from web.services.password_confirm_throttle import render_lockout
from web.services.profile_service import update_email, update_fullname, update_password
from web.services.user_media_service import get_user_media

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
        "audio_error": None,
        "user_media": None,
        **overrides,
    }


def _html_response(response: Any) -> HTMLResponse:
    return cast(HTMLResponse, response)


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
# Profile page
# ---------------------------------------------------------------------------


@router.get("/profile", response_class=HTMLResponse)
async def profile_get(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    actor, contest = await _resolve_profile_user_and_contest(request)
    async with request.app.state.db_session() as session:
        site_name = await _resolve_user_site_name(session, actor)
        user_media = await get_user_media(session, actor.id) if isinstance(actor, User) else None
    return _html_response(
        templates.TemplateResponse(
            request,
            _PROFILE_TEMPLATE,
            _profile_context(actor, contest=contest, site_name=site_name, user_media=user_media),
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
            user_media = await get_user_media(session, actor.id) if isinstance(actor, User) else None
            return _html_response(
                templates.TemplateResponse(
                    request,
                    _PROFILE_TEMPLATE,
                    _profile_context(
                        actor,
                        contest=contest,
                        site_name=site_name,
                        user_media=user_media,
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
            user_media = await get_user_media(session, actor.id) if isinstance(actor, User) else None
            return _html_response(
                templates.TemplateResponse(
                    request,
                    _PROFILE_TEMPLATE,
                    _profile_context(
                        actor,
                        contest=contest,
                        site_name=site_name,
                        user_media=user_media,
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
            user_media = await get_user_media(session, actor.id) if isinstance(actor, User) else None
            return _html_response(
                templates.TemplateResponse(
                    request,
                    _PROFILE_TEMPLATE,
                    _profile_context(
                        actor,
                        contest=contest,
                        site_name=site_name,
                        user_media=user_media,
                        password_error="New passwords do not match.",
                    ),
                    status_code=422,
                )
            )

        # The current password is verified through the shared reconfirmation
        # budget (lockout first, then the hash), so the service is called without
        # ``current_password`` and the hash is checked exactly once.
        confirmation = await verify_current_password(
            request, session, actor=actor, password=current_password, action="profile_password"
        )
        if confirmation.locked:
            return render_lockout(
                request,
                retry_after_seconds=confirmation.retry_after_seconds,
                back_url="/profile",
                back_label="Back to profile",
            )
        error = None if confirmation.ok else "Current password is incorrect."
        if error is None:
            error = await update_password(session, actor, new_password)
        if error:
            site_name = await _resolve_user_site_name(session, actor)
            user_media = await get_user_media(session, actor.id) if isinstance(actor, User) else None
            return _html_response(
                templates.TemplateResponse(
                    request,
                    _PROFILE_TEMPLATE,
                    _profile_context(
                        actor,
                        contest=contest,
                        site_name=site_name,
                        user_media=user_media,
                        password_error=error,
                    ),
                    status_code=422,
                )
            )

    flash("Password changed successfully.", FlashCategory.SUCCESS)
    return RedirectResponse(url="/profile", status_code=303)
