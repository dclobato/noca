#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena class browser and management routes."""

from __future__ import annotations

from datetime import UTC, date, datetime
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
    arena_class_service,
    arena_problem_set_service,
    arena_student_problem_set_service,
)
from arena.services.arena_class_service import (
    ArenaClassNotFoundError,
    ArenaClassPermissionError,
    ArenaClassValidationError,
)
from arena.services.arena_problem_set_service import (
    ArenaProblemSetPermissionError,
    _class_end_bound,
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
    """Return the current local date used for class date validation."""
    return date.today()


def _parse_date(value: str) -> date:
    """Parse a browser date input value."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ArenaClassValidationError("Invalid class date.") from exc


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


def _render(request: Request, template_name: str, context: dict[str, Any]) -> HTMLResponse:
    """Render a template with the given context."""
    return _html(request.app.state.arena_templates.TemplateResponse(request, template_name, context))


async def _render_class_form(
    request: Request,
    *,
    current_user: ArenaUser,
    class_id: str | None = None,
    errors: list[str] | None = None,
    form_data: dict[str, Any] | None = None,
    truncation_warning: list[str] | None = None,
    session: AsyncSession,
) -> HTMLResponse:
    """Render the class create/edit form."""
    today = _today()
    detail = None
    teacher_label = ""
    if class_id is not None:
        detail = await arena_class_detail_service.get_class_detail(session, class_id=class_id, today=today)
        if current_user.role != ArenaRole.ARENA_ADMIN and detail.teacher_id != current_user.id:
            raise HTTPException(status_code=403, detail="Forbidden")
        teacher_label = f"{detail.teacher_name} <{detail.teacher_email}>"

    can_edit_start = detail is None or detail.starts_on > today
    can_edit_end = detail is None or detail.finishes_on >= today
    is_judge = current_user.role == ArenaRole.ARENA_JUDGE
    judge_label = f"{current_user.nome} <{current_user.email_normalizado}>" if is_judge else ""
    judge_user_id = str(current_user.id) if is_judge else ""
    return _render(
        request,
        "classes/class_form.html",
        {
            "current_user": current_user,
            "class_detail": detail,
            "form_data": form_data or {},
            "errors": errors or [],
            "today": today,
            "teacher_label": teacher_label,
            "is_admin": current_user.role == ArenaRole.ARENA_ADMIN,
            "is_judge": is_judge,
            "judge_label": judge_label,
            "judge_user_id": judge_user_id,
            "can_edit_start": can_edit_start,
            "can_edit_end": can_edit_end,
            "truncation_warning": truncation_warning or [],
        },
    )


_TAB_REDIRECT: dict[str, str] = {
    "registered": "arena_classes_registered",
    "open": "arena_classes_open",
    "manage": "arena_classes_manage",
}


@router.get("/classes", response_class=HTMLResponse, name="arena_classes_index")
async def classes_index(
    request: Request,
    tab: str | None = None,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the Classes landing page. Redirects legacy ?tab= bookmarks to their new URLs."""
    user_or_redirect = _require_user(request, current_user)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    user = user_or_redirect
    if tab in _TAB_REDIRECT:
        # Non-managers landing on ?tab=manage fall back to registered, not 403.
        route_name = _TAB_REDIRECT[tab] if tab != "manage" or _is_manager(user) else "arena_classes_registered"
        return RedirectResponse(url=str(request.url_for(route_name)), status_code=301)
    return _render(request, "classes/index.html", {"current_user": user, "is_manager": _is_manager(user)})


@router.get("/classes/registered", response_class=HTMLResponse, name="arena_classes_registered")
async def classes_registered(
    request: Request,
    page: str | None = None,
    search: str | None = None,
    sort: str | None = None,
    direction: str | None = None,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the registered classes page."""
    user_or_redirect = _require_user(request, current_user)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    user = user_or_redirect
    registered = await arena_class_query_service.list_user_class_rows_paginated(
        session,
        user_id=user.id,
        today=_today(),
        params=build_pagination_params(page, per_page=_CLASSES_PER_PAGE),
        search=search or "",
        sort=arena_class_query_service.normalize_class_sort(sort, "name"),
        direction=arena_class_query_service.normalize_sort_dir(direction, "asc"),
    )
    return _render(
        request,
        "classes/registered.html",
        {
            "current_user": user,
            "registered": registered,
            "search": search or "",
            "sort": sort or "name",
            "dir": direction or "asc",
        },
    )


@router.get("/classes/open", response_class=HTMLResponse, name="arena_classes_open")
async def classes_open(
    request: Request,
    page: str | None = None,
    search: str | None = None,
    teacher_id: str | None = None,
    sort: str | None = None,
    direction: str | None = None,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the open classes page."""
    user_or_redirect = _require_user(request, current_user)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    user = user_or_redirect
    open_classes = await arena_class_query_service.list_open_class_rows_paginated(
        session,
        user_id=user.id,
        user_affiliation_id=user.affiliation_id,
        actor_role=user.role,
        today=_today(),
        params=build_pagination_params(page, per_page=_CLASSES_PER_PAGE),
        search=search or "",
        teacher_id=teacher_id or None,
        sort=arena_class_query_service.normalize_class_sort(sort, "starts_on"),
        direction=arena_class_query_service.normalize_sort_dir(direction, "desc"),
    )
    teacher_label = ""
    if teacher_id:
        teacher = await session.get(ArenaUser, teacher_id)
        if teacher is not None:
            teacher_label = f"{teacher.nome} <{teacher.email_normalizado}>"
    return _render(
        request,
        "classes/open.html",
        {
            "current_user": user,
            "open_classes": open_classes,
            "search": search or "",
            "teacher_id": teacher_id or "",
            "teacher_label": teacher_label,
            "sort": sort or "starts_on",
            "dir": direction or "desc",
        },
    )


@router.get("/classes/manage", response_class=HTMLResponse, name="arena_classes_manage")
async def classes_manage(
    request: Request,
    page: str | None = None,
    search: str | None = None,
    sort: str | None = None,
    direction: str | None = None,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the manage classes page."""
    user_or_redirect = _require_user(request, current_user)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    user = user_or_redirect
    if not _is_manager(user):
        # Intentional 403: /classes/manage is a hard access boundary, not a silent UI fallback.
        raise HTTPException(status_code=403, detail="Forbidden")
    managed = await arena_class_query_service.list_managed_class_rows_paginated(
        session,
        actor_id=user.id,
        actor_role=user.role,
        today=_today(),
        params=build_pagination_params(page, per_page=_CLASSES_PER_PAGE),
        search=search or "",
        sort=arena_class_query_service.normalize_class_sort(sort, "name"),
        direction=arena_class_query_service.normalize_sort_dir(direction, "asc"),
    )
    return _render(
        request,
        "classes/manage.html",
        {
            "current_user": user,
            "managed": managed,
            "is_admin": user.role == ArenaRole.ARENA_ADMIN,
            "search": search or "",
            "sort": sort or "name",
            "dir": direction or "asc",
        },
    )


@router.get("/classes/new", response_class=HTMLResponse, name="arena_class_new")
async def class_new(
    request: Request,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the new class form."""
    user_or_redirect = _require_user(request, current_user)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    if not _is_manager(user_or_redirect):
        raise HTTPException(status_code=403, detail="Forbidden")
    return await _render_class_form(request, current_user=user_or_redirect, session=session)


@router.post("/classes/new", name="arena_class_create")
async def class_create(
    request: Request,
    flash: FlashDep,
    name: str = Form(""),
    description: str = Form(""),
    starts_on: str = Form(""),
    finishes_on: str = Form(""),
    allow_self_registration: str | None = Form(None),
    teacher_id: str = Form(""),
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Create a class and redirect to the manage page."""
    user_or_redirect = _require_user(request, current_user)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    user = user_or_redirect
    if not _is_manager(user):
        raise HTTPException(status_code=403, detail="Forbidden")
    form_data = {
        "name": name,
        "description": description,
        "starts_on": starts_on,
        "finishes_on": finishes_on,
        "allow_self_registration": bool(allow_self_registration),
        "teacher_id": teacher_id,
    }
    try:
        today = _today()
        parsed_starts = _parse_date(starts_on)
        parsed_finishes = _parse_date(finishes_on)
        if parsed_starts < today or parsed_finishes < today:
            raise ArenaClassValidationError("Class dates cannot be in the past.")
        arena_class = await arena_class_service.create_class(
            session,
            actor_id=user.id,
            actor_role=user.role,
            name=name,
            starts_on=parsed_starts,
            finishes_on=parsed_finishes,
            description=description,
            teacher_id=teacher_id or None,
            allow_self_registration=bool(allow_self_registration),
        )
        await session.commit()
    except ArenaClassValidationError as exc:
        await session.rollback()
        await session.refresh(user)
        return await _render_class_form(
            request,
            current_user=user,
            errors=[str(exc)],
            form_data=form_data,
            session=session,
        )
    flash("Class created.", FlashCategory.SUCCESS)
    manage_url = str(request.url_for("arena_classes_manage"))
    return _redirect_with_hash(manage_url, f"class-{arena_class.id}")


@router.get("/classes/teachers/autocomplete", name="arena_class_teacher_autocomplete")
async def class_teacher_autocomplete(
    q: str = "",
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return judge suggestions for class teacher autocomplete fields."""
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    affiliation_id = None if current_user.role == ArenaRole.ARENA_ADMIN else current_user.affiliation_id
    if current_user.role != ArenaRole.ARENA_ADMIN and affiliation_id is None:
        return JSONResponse({"teachers": []})
    rows = await arena_class_detail_service.search_teacher_autocomplete(
        session,
        query=q,
        affiliation_id=affiliation_id,
    )
    return JSONResponse({"teachers": [{"id": row.user_id, "label": row.label} for row in rows]})


@router.get("/classes/{class_id}", response_class=HTMLResponse, name="arena_class_detail")
async def class_detail(
    request: Request,
    class_id: str,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render a class detail page."""
    user_or_redirect = _require_user(request, current_user)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    try:
        detail = await arena_class_detail_service.get_class_detail(session, class_id=class_id, today=_today())
    except ArenaClassNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Class not found") from exc
    is_registered = await arena_class_membership_service.is_active_member(
        session,
        class_id=class_id,
        user_id=user_or_redirect.id,
    )
    if (
        user_or_redirect.role != ArenaRole.ARENA_ADMIN
        and detail.teacher_id != user_or_redirect.id
        and not is_registered
        and not detail.allow_self_registration
    ):
        raise HTTPException(status_code=403, detail="Forbidden")
    can_see_problem_sets = (
        user_or_redirect.role == ArenaRole.ARENA_ADMIN or detail.teacher_id == user_or_redirect.id or is_registered
    )
    problem_sets = None
    if can_see_problem_sets:
        try:
            problem_sets = await arena_student_problem_set_service.list_student_problem_sets_paginated(
                session,
                actor_id=user_or_redirect.id,
                actor_role=user_or_redirect.role,
                class_id=class_id,
                now=datetime.now(UTC),
                params=build_pagination_params(None, per_page=200),
            )
        except ArenaProblemSetPermissionError:
            problem_sets = None
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "classes/class_detail.html",
            {
                "current_user": user_or_redirect,
                "class_detail": detail,
                "is_registered": is_registered,
                "problem_sets": problem_sets,
            },
        )
    )


@router.post("/classes/{class_id}/request-registration", name="arena_class_request_registration")
async def class_request_registration(
    request: Request,
    class_id: str,
    flash: FlashDep,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Request self-registration for a class."""
    user_or_redirect = _require_user(request, current_user)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    try:
        reg_request = await arena_class_membership_service.request_registration(
            session,
            user_id=user_or_redirect.id,
            class_id=class_id,
        )
        class_detail = await arena_class_detail_service.get_class_detail(session, class_id=class_id, today=_today())
        members_url = str(request.url_for("arena_class_members", class_id=class_id))
        await create_arena_notification(
            session,
            user_id=class_detail.teacher_id,
            notification_kind=ArenaNotificationKind.CLASS_REGISTRATION_REQUEST,
            title="New registration request",
            message=(
                f'{user_or_redirect.nome} has requested to join "{class_detail.name}". Review the pending request.'
            ),
            target_url=members_url,
            source_ref=reg_request.id,
        )
        await session.commit()
        arena_class_email_service.send_class_registration_request_email(
            teacher_email=class_detail.teacher_email,
            teacher_name=class_detail.teacher_name,
            student_name=user_or_redirect.nome,
            class_name=class_detail.name,
            members_url=members_url,
            email_service=request.app.state.email_service,
        )
    except (ArenaClassNotFoundError, ArenaClassValidationError) as exc:
        await session.rollback()
        flash(str(exc), FlashCategory.WARNING)
        open_url = str(request.url_for("arena_classes_open"))
        return _redirect_with_hash(open_url, f"class-{class_id}")
    flash("Registration request sent.", FlashCategory.SUCCESS)
    registered_url = str(request.url_for("arena_classes_registered"))
    return _redirect_with_hash(registered_url, f"class-{class_id}")


@router.get("/classes/{class_id}/edit", response_class=HTMLResponse, name="arena_class_edit")
async def class_edit(
    request: Request,
    class_id: str,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the class edit form."""
    user_or_redirect = _require_user(request, current_user)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    if not _is_manager(user_or_redirect):
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        return await _render_class_form(
            request,
            current_user=user_or_redirect,
            class_id=class_id,
            session=session,
        )
    except ArenaClassNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Class not found") from exc


@router.post("/classes/{class_id}/edit", name="arena_class_update")
async def class_update(
    request: Request,
    class_id: str,
    flash: FlashDep,
    name: str = Form(""),
    description: str = Form(""),
    starts_on: str = Form(""),
    finishes_on: str = Form(""),
    allow_self_registration: str | None = Form(None),
    teacher_id: str = Form(""),
    confirm_truncate: str | None = Form(None),
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Update class metadata."""
    user_or_redirect = _require_user(request, current_user)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    user = user_or_redirect
    if not _is_manager(user):
        raise HTTPException(status_code=403, detail="Forbidden")
    form_data = {
        "name": name,
        "description": description,
        "starts_on": starts_on,
        "finishes_on": finishes_on,
        "allow_self_registration": bool(allow_self_registration),
        "teacher_id": teacher_id,
    }
    new_finishes_on = _parse_date(finishes_on)
    if not confirm_truncate:
        cutoff_utc = _class_end_bound(new_finishes_on)
        affected = await arena_problem_set_service.find_problem_sets_exceeding_deadline(
            session, class_id=class_id, cutoff=cutoff_utc
        )
        if affected:
            return await _render_class_form(
                request,
                current_user=user,
                class_id=class_id,
                form_data=form_data,
                truncation_warning=[ps.name for ps in affected],
                session=session,
            )
    try:
        await arena_class_service.update_class(
            session,
            actor_id=user.id,
            actor_role=user.role,
            class_id=class_id,
            today=_today(),
            name=name,
            starts_on=_parse_date(starts_on),
            finishes_on=new_finishes_on,
            description=description,
            teacher_id=teacher_id or None,
            allow_self_registration=bool(allow_self_registration),
        )
        if confirm_truncate:
            cutoff_utc = _class_end_bound(new_finishes_on)
            await arena_problem_set_service.truncate_problem_set_deadlines(
                session, class_id=class_id, cutoff=cutoff_utc
            )
        await session.commit()
    except ArenaClassNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Class not found") from exc
    except (ArenaClassPermissionError, ArenaClassValidationError) as exc:
        await session.rollback()
        await session.refresh(user)
        return await _render_class_form(
            request,
            current_user=user,
            class_id=class_id,
            errors=[str(exc)],
            form_data=form_data,
            session=session,
        )
    flash("Class updated.", FlashCategory.SUCCESS)
    manage_url = str(request.url_for("arena_classes_manage"))
    return _redirect_with_hash(manage_url, f"class-{class_id}")
