#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena class membership management routes."""

from __future__ import annotations

from datetime import date
from typing import Any, cast

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from arena.services import (
    arena_class_detail_service,
    arena_class_email_service,
    arena_class_membership_service,
    arena_class_query_service,
)
from arena.services.arena_class_service import (
    ArenaClassNotFoundError,
    ArenaClassPermissionError,
    ArenaClassValidationError,
)
from arena.services.pagination_service import build_pagination_params
from arena.services.session_service import build_current_next_url, build_login_redirect_response
from shared.enumerations import ArenaNotificationKind, ArenaRole
from shared.services.arena_notification_service import create_arena_notification

router = APIRouter(tags=["arena-classes"])

_CLASSES_PER_PAGE = 25


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def _today() -> date:
    """Return the current local date."""
    return date.today()


def _require_user(request: Request, current_user: ArenaUser | None) -> ArenaUser | RedirectResponse:
    """Return the current user or a login redirect response."""
    if current_user is None:
        return build_login_redirect_response(request, next_url=build_current_next_url(request))
    return current_user


def _is_manager(user: ArenaUser) -> bool:
    """Return whether the user can manage Arena classes."""
    return user.role in {ArenaRole.ARENA_ADMIN, ArenaRole.ARENA_JUDGE}


def _redirect_with_hash(url: str, fragment: str) -> RedirectResponse:
    """Build a 303 redirect with a URL fragment."""
    return RedirectResponse(url=f"{url}#{fragment}", status_code=303)


@router.get("/classes/{class_id}/members", response_class=HTMLResponse, name="arena_class_members")
async def class_members(
    request: Request,
    class_id: str,
    page: str | None = None,
    sort: str | None = None,
    direction: str | None = None,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render class membership management."""
    user_or_redirect = _require_user(request, current_user)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    if not _is_manager(user_or_redirect):
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        detail = await arena_class_detail_service.get_class_detail(session, class_id=class_id, today=_today())
        pagination = await arena_class_detail_service.list_class_members_management_paginated(
            session,
            actor_id=user_or_redirect.id,
            actor_role=user_or_redirect.role,
            class_id=class_id,
            params=build_pagination_params(page, per_page=_CLASSES_PER_PAGE),
            sort=arena_class_query_service.normalize_member_sort(sort),
            direction=arena_class_query_service.normalize_sort_dir(direction, "asc"),
        )
    except ArenaClassNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Class not found") from exc
    except ArenaClassPermissionError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "classes/class_members.html",
            {
                "current_user": user_or_redirect,
                "class_detail": detail,
                "pagination": pagination,
                "sort": sort or "name",
                "direction": direction or "asc",
                "student_autocomplete_url": str(
                    request.url_for("arena_class_member_student_autocomplete", class_id=class_id)
                ),
            },
        )
    )


@router.get("/classes/{class_id}/members/autocomplete", name="arena_class_member_student_autocomplete")
async def class_member_student_autocomplete(
    class_id: str,
    q: str = "",
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return eligible student suggestions for class membership assignment."""
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not _is_manager(current_user):
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        rows = await arena_class_detail_service.search_student_autocomplete(
            session,
            actor_id=current_user.id,
            actor_role=current_user.role,
            class_id=class_id,
            query=q,
        )
    except ArenaClassNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Class not found") from exc
    except ArenaClassPermissionError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    return JSONResponse({"students": [{"id": row.user_id, "label": row.label} for row in rows]})


@router.post("/classes/{class_id}/members", name="arena_class_members_add")
async def class_members_add(
    request: Request,
    class_id: str,
    flash: FlashDep,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Assign one or more selected students to a class."""
    user_or_redirect = _require_user(request, current_user)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    if not _is_manager(user_or_redirect):
        raise HTTPException(status_code=403, detail="Forbidden")
    form = await request.form()
    raw_ids = [str(user_id) for user_id in form.getlist("student_ids") if str(user_id).strip()]
    unique_ids = list(dict.fromkeys(raw_ids))
    try:
        class_detail = await arena_class_detail_service.get_class_detail(session, class_id=class_id, today=_today())
        count = await arena_class_membership_service.assign_users(
            session,
            actor_id=user_or_redirect.id,
            actor_role=user_or_redirect.role,
            class_id=class_id,
            user_ids=unique_ids,
            on_date=_today(),
        )
        class_url = str(request.url_for("arena_class_detail", class_id=class_id))
        for uid in unique_ids:
            await create_arena_notification(
                session,
                user_id=uid,
                notification_kind=ArenaNotificationKind.CLASS_MEMBERSHIP_ADDED,
                title="Added to class",
                message=f'You have been added to "{class_detail.name}".',
                target_url=class_url,
                source_ref=f"{class_id}:{uid}:{_today()}",
            )
        await session.commit()
        for uid in unique_ids:
            added_user = await session.get(ArenaUser, uid)
            if added_user is not None:
                arena_class_email_service.send_class_membership_added_email(
                    student_email=added_user.email_normalizado,
                    student_name=added_user.nome,
                    class_name=class_detail.name,
                    class_url=class_url,
                    email_service=request.app.state.email_service,
                )
        flash(f"{count} student{'s' if count != 1 else ''} added.", FlashCategory.SUCCESS)
    except (ArenaClassNotFoundError, ArenaClassPermissionError, ArenaClassValidationError) as exc:
        await session.rollback()
        flash(str(exc), FlashCategory.WARNING)
    return _redirect_with_hash(str(request.url_for("arena_class_members", class_id=class_id)), "students")


@router.post("/classes/registration-requests/{request_id}/approve", name="arena_class_request_approve")
async def class_request_approve(
    request: Request,
    request_id: str,
    flash: FlashDep,
    class_id: str = Form(""),
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Approve a class registration request."""
    user_or_redirect = _require_user(request, current_user)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    try:
        decided = await arena_class_membership_service.decide_registration(
            session,
            actor_id=user_or_redirect.id,
            actor_role=user_or_redirect.role,
            request_id=request_id,
            approve=True,
            on_date=_today(),
        )
        class_id = decided.class_id
        class_detail = await arena_class_detail_service.get_class_detail(session, class_id=class_id, today=_today())
        class_url = str(request.url_for("arena_class_detail", class_id=class_id))
        await create_arena_notification(
            session,
            user_id=decided.user_id,
            notification_kind=ArenaNotificationKind.CLASS_REGISTRATION_APPROVED,
            title="Registration approved",
            message=f'Your request to join "{class_detail.name}" was approved.',
            target_url=class_url,
            source_ref=decided.id,
        )
        await session.commit()
        approved_user = await session.get(ArenaUser, decided.user_id)
        if approved_user is not None:
            arena_class_email_service.send_class_registration_approved_email(
                student_email=approved_user.email_normalizado,
                student_name=approved_user.nome,
                class_name=class_detail.name,
                class_url=class_url,
                email_service=request.app.state.email_service,
            )
        flash("Registration approved.", FlashCategory.SUCCESS)
    except (ArenaClassNotFoundError, ArenaClassPermissionError, ArenaClassValidationError) as exc:
        await session.rollback()
        flash(str(exc), FlashCategory.WARNING)
    return _redirect_with_hash(str(request.url_for("arena_class_members", class_id=class_id)), f"request-{request_id}")


@router.post("/classes/registration-requests/{request_id}/deny", name="arena_class_request_deny")
async def class_request_deny(
    request: Request,
    request_id: str,
    flash: FlashDep,
    class_id: str = Form(""),
    reason: str = Form(""),
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Deny a class registration request."""
    user_or_redirect = _require_user(request, current_user)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    try:
        decided = await arena_class_membership_service.decide_registration(
            session,
            actor_id=user_or_redirect.id,
            actor_role=user_or_redirect.role,
            request_id=request_id,
            approve=False,
            on_date=_today(),
            reason=reason,
        )
        class_id = decided.class_id
        class_detail = await arena_class_detail_service.get_class_detail(session, class_id=class_id, today=_today())
        denial_suffix = f" Reason: {decided.denial_reason}" if decided.denial_reason else ""
        await create_arena_notification(
            session,
            user_id=decided.user_id,
            notification_kind=ArenaNotificationKind.CLASS_REGISTRATION_DENIED,
            title="Registration denied",
            message=f'Your request to join "{class_detail.name}" was denied.{denial_suffix}',
            target_url=str(request.url_for("arena_classes_registered")),
            source_ref=decided.id,
        )
        await session.commit()
        denied_user = await session.get(ArenaUser, decided.user_id)
        if denied_user is not None:
            arena_class_email_service.send_class_registration_denied_email(
                student_email=denied_user.email_normalizado,
                student_name=denied_user.nome,
                class_name=class_detail.name,
                denial_reason=decided.denial_reason,
                email_service=request.app.state.email_service,
            )
        flash("Registration denied.", FlashCategory.SUCCESS)
    except (ArenaClassNotFoundError, ArenaClassPermissionError, ArenaClassValidationError) as exc:
        await session.rollback()
        flash(str(exc), FlashCategory.WARNING)
    return _redirect_with_hash(str(request.url_for("arena_class_members", class_id=class_id)), f"request-{request_id}")


@router.post("/classes/{class_id}/members/{user_id}/remove", name="arena_class_member_remove")
async def class_member_remove(
    request: Request,
    class_id: str,
    user_id: str,
    flash: FlashDep,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Remove an active class membership."""
    user_or_redirect = _require_user(request, current_user)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    try:
        await arena_class_membership_service.remove_users(
            session,
            actor_id=user_or_redirect.id,
            actor_role=user_or_redirect.role,
            class_id=class_id,
            user_ids=[user_id],
            on_date=_today(),
        )
        if user_id != user_or_redirect.id:
            class_detail = await arena_class_detail_service.get_class_detail(session, class_id=class_id, today=_today())
            await create_arena_notification(
                session,
                user_id=user_id,
                notification_kind=ArenaNotificationKind.CLASS_MEMBERSHIP_REMOVED,
                title="Removed from class",
                message=f'You have been removed from "{class_detail.name}".',
                target_url=str(request.url_for("arena_classes_registered")),
                source_ref=f"{class_id}:{user_id}:{_today()}",
            )
            await session.commit()
            removed_user = await session.get(ArenaUser, user_id)
            if removed_user is not None:
                arena_class_email_service.send_class_membership_removed_email(
                    student_email=removed_user.email_normalizado,
                    student_name=removed_user.nome,
                    class_name=class_detail.name,
                    email_service=request.app.state.email_service,
                )
        else:
            await session.commit()
        flash("Membership removed.", FlashCategory.SUCCESS)
    except (ArenaClassNotFoundError, ArenaClassPermissionError, ArenaClassValidationError) as exc:
        await session.rollback()
        flash(str(exc), FlashCategory.WARNING)
    return _redirect_with_hash(str(request.url_for("arena_class_members", class_id=class_id)), f"user-{user_id}")
