#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena teacher problem-set management routes."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, cast
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_problem_sets import ArenaProblemSet
from arena.models.arena_users import ArenaUser
from arena.services import (
    arena_batch_feedback_service,
    arena_class_detail_service,
    arena_problem_set_management_service,
    arena_problem_set_service,
)
from arena.services.arena_class_service import ArenaClassNotFoundError
from arena.services.arena_problem_set_service import (
    ArenaProblemSetNotFoundError,
    ArenaProblemSetPermissionError,
    ArenaProblemSetValidationError,
    stop_problem_set_now,
    update_problem_set_details,
)
from arena.services.pagination_service import build_pagination_params
from arena.services.session_service import build_current_next_url, build_login_redirect_response
from arena.services.user_timezone_service import parse_user_datetime_local
from shared.enumerations import ArenaRole

router = APIRouter(tags=["arena-classes"])


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def _require_user(request: Request, current_user: ArenaUser | None) -> ArenaUser | RedirectResponse:
    """Return the current user or a login redirect response."""
    if current_user is None:
        return build_login_redirect_response(request, next_url=build_current_next_url(request))
    return current_user


def _is_manager(user: ArenaUser) -> bool:
    """Return whether the user can manage Arena classes."""
    return user.role in {ArenaRole.ARENA_ADMIN, ArenaRole.ARENA_JUDGE}


def _parse_datetime_local(value: str, current_user: ArenaUser) -> datetime | None:
    """Parse a browser datetime-local value in the user's timezone as UTC."""
    try:
        return parse_user_datetime_local(value, current_user)
    except ValueError as exc:
        raise ArenaProblemSetValidationError(str(exc)) from exc


def _problem_set_list_url(
    request: Request,
    *,
    class_id: str,
    page: int | str | None = None,
    sort: str | None = None,
    direction: str | None = None,
) -> str:
    """Build a problem-set list URL with optional pagination/sort context."""
    params = {
        key: str(value)
        for key, value in {
            "page": page,
            "sort": sort,
            "direction": direction,
        }.items()
        if value not in {None, ""}
    }
    url = str(request.url_for("arena_class_problem_set_list", class_id=class_id))
    return f"{url}?{urlencode(params)}" if params else url


async def _require_problem_set_manager(
    request: Request,
    current_user: ArenaUser | None,
    *,
    class_id: str,
    session: AsyncSession,
) -> tuple[ArenaUser | RedirectResponse, Any | None]:
    """Return the logged-in teacher/admin and the class detail for a class-scoped page."""
    user_or_redirect = _require_user(request, current_user)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect, None
    if not _is_manager(user_or_redirect):
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        detail = await arena_class_detail_service.get_class_detail(session, class_id=class_id, today=date.today())
    except ArenaClassNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Class not found") from exc
    if user_or_redirect.role != ArenaRole.ARENA_ADMIN and detail.teacher_id != user_or_redirect.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return user_or_redirect, detail


async def _render_problem_set_list_page(
    request: Request,
    *,
    current_user: ArenaUser,
    class_detail: Any,
    session: AsyncSession,
    page: str | None,
    sort: str | None,
    direction: str | None,
) -> HTMLResponse:
    """Render the teacher problem-set list page."""
    pagination = await arena_problem_set_management_service.list_problem_sets_paginated(
        session,
        actor_id=current_user.id,
        actor_role=current_user.role,
        class_id=class_detail.class_id,
        now=datetime.now(UTC),
        params=build_pagination_params(page, per_page=25),
        sort=arena_problem_set_management_service.normalize_problem_set_sort(sort, "deadline"),
        direction=arena_problem_set_management_service.normalize_sort_dir(direction, "desc"),
    )
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "classes/problem_set_list.html",
            {
                "current_user": current_user,
                "class_detail": class_detail,
                "pagination": pagination,
                "sort": sort or "deadline",
                "direction": direction or "desc",
            },
        )
    )


@router.get(
    "/classes/{class_id}/problem-sets",
    response_class=HTMLResponse,
    name="arena_class_problem_set_list",
)
async def class_problem_set_list(
    request: Request,
    class_id: str,
    page: str | None = None,
    sort: str | None = None,
    direction: str | None = None,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the teacher problem-set list for one class."""
    user_or_redirect, class_detail = await _require_problem_set_manager(
        request,
        current_user,
        class_id=class_id,
        session=session,
    )
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    try:
        return await _render_problem_set_list_page(
            request,
            current_user=user_or_redirect,
            class_detail=class_detail,
            session=session,
            page=page,
            sort=sort,
            direction=direction,
        )
    except (ArenaProblemSetNotFoundError, ArenaProblemSetPermissionError) as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc


@router.post("/classes/{class_id}/problem-sets", name="arena_class_problem_set_create")
async def class_problem_set_create(
    request: Request,
    class_id: str,
    flash: FlashDep,
    name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    starts_on: Annotated[str, Form()] = "",
    deadline: Annotated[str, Form()] = "",
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Create a new problem set and redirect to the manage-problems page."""
    user_or_redirect, class_detail = await _require_problem_set_manager(
        request,
        current_user,
        class_id=class_id,
        session=session,
    )
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    try:
        problem_set = await arena_problem_set_service.create_problem_set(
            session,
            actor_id=user_or_redirect.id,
            actor_role=user_or_redirect.role,
            class_id=class_id,
            name=name,
            description=description,
        )
        await arena_problem_set_service.set_problem_set_schedule(
            session,
            actor_id=user_or_redirect.id,
            actor_role=user_or_redirect.role,
            set_id=problem_set.id,
            starts_on=_parse_datetime_local(starts_on, user_or_redirect),
            deadline=_parse_datetime_local(deadline, user_or_redirect),
            now=datetime.now(UTC),
        )
        await session.commit()
    except (ArenaProblemSetNotFoundError, ArenaProblemSetPermissionError) as exc:
        await session.rollback()
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    except ArenaProblemSetValidationError as exc:
        await session.rollback()
        await session.refresh(user_or_redirect)
        flash(str(exc), FlashCategory.WARNING)
        return await _render_problem_set_list_page(
            request,
            current_user=user_or_redirect,
            class_detail=class_detail,
            session=session,
            page="1",
            sort=None,
            direction=None,
        )
    flash("Problem set created.", FlashCategory.SUCCESS)
    return RedirectResponse(
        url=str(request.url_for("arena_class_problem_set_manage", class_id=class_id, set_id=problem_set.id)),
        status_code=303,
    )


@router.get(
    "/classes/{class_id}/problem-sets/{set_id}/problems",
    response_class=HTMLResponse,
    name="arena_class_problem_set_manage",
)
async def class_problem_set_manage(
    request: Request,
    class_id: str,
    set_id: str,
    page: str | None = None,
    sort: str | None = None,
    direction: str | None = None,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the teacher problem-set manage-problems page."""
    user_or_redirect, class_detail = await _require_problem_set_manager(
        request,
        current_user,
        class_id=class_id,
        session=session,
    )
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    try:
        problem_rows = await arena_problem_set_management_service.list_problem_set_problems(
            session,
            actor_id=user_or_redirect.id,
            actor_role=user_or_redirect.role,
            set_id=set_id,
        )
        problem_set = await session.get(ArenaProblemSet, set_id)
        if problem_set is None or problem_set.class_id != class_id:
            raise HTTPException(status_code=404, detail="Problem set not found")
        non_ac_counts = await arena_batch_feedback_service.get_non_ac_counts_for_set(
            session,
            actor_id=user_or_redirect.id,
            actor_role=user_or_redirect.role,
            set_id=set_id,
        )
    except ArenaProblemSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Problem set not found") from exc
    except ArenaProblemSetPermissionError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    templates = request.app.state.arena_templates
    now = datetime.now(UTC)
    started = problem_set.starts_on is None or problem_set.starts_on <= now
    not_closed = problem_set.deadline is None or problem_set.deadline > now
    is_accepting = started and not_closed
    return _html(
        templates.TemplateResponse(
            request,
            "classes/problem_set_manage.html",
            {
                "current_user": user_or_redirect,
                "class_detail": class_detail,
                "problem_set": problem_set,
                "problem_rows": problem_rows,
                "non_ac_counts": non_ac_counts,
                "is_accepting": is_accepting,
                "back_url": _problem_set_list_url(
                    request,
                    class_id=class_id,
                    page=page,
                    sort=sort,
                    direction=direction,
                ),
                "autocomplete_url": str(
                    request.url_for("arena_class_problem_set_problem_autocomplete", class_id=class_id, set_id=set_id)
                ),
            },
        )
    )


@router.post(
    "/classes/{class_id}/problem-sets/{set_id}/problems",
    name="arena_class_problem_set_problem_add",
)
async def class_problem_set_problem_add(
    request: Request,
    class_id: str,
    set_id: str,
    flash: FlashDep,
    problem_ref: Annotated[str, Form()] = "",
    page: Annotated[str, Form()] = "1",
    sort: Annotated[str, Form()] = "deadline",
    direction: Annotated[str, Form()] = "desc",
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Add a problem to the current problem set."""
    user_or_redirect, _class_detail = await _require_problem_set_manager(
        request,
        current_user,
        class_id=class_id,
        session=session,
    )
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    form = await request.form()
    problem_refs = [str(ref) for ref in form.getlist("problem_refs")]
    refs = [ref for ref in problem_refs if ref.strip()]
    if problem_ref.strip():
        refs.append(problem_ref)
    try:
        await arena_problem_set_service.add_problems_to_set(
            session,
            actor_id=user_or_redirect.id,
            actor_role=user_or_redirect.role,
            set_id=set_id,
            refs=refs,
        )
        await session.commit()
        flash("Problems added to the problem set.", FlashCategory.SUCCESS)
    except (ArenaProblemSetNotFoundError, ArenaProblemSetPermissionError) as exc:
        await session.rollback()
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    except ArenaProblemSetValidationError as exc:
        await session.rollback()
        flash(str(exc), FlashCategory.WARNING)
    return RedirectResponse(
        url=str(request.url_for("arena_class_problem_set_manage", class_id=class_id, set_id=set_id)),
        status_code=303,
    )


@router.post(
    "/classes/{class_id}/problem-sets/{set_id}/problems/{problem_id}/remove",
    name="arena_class_problem_set_problem_remove",
)
async def class_problem_set_problem_remove(
    request: Request,
    class_id: str,
    set_id: str,
    problem_id: str,
    flash: FlashDep,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Remove a problem from a problem set."""
    user_or_redirect, _class_detail = await _require_problem_set_manager(
        request,
        current_user,
        class_id=class_id,
        session=session,
    )
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    try:
        await arena_problem_set_service.remove_problems_from_set(
            session,
            actor_id=user_or_redirect.id,
            actor_role=user_or_redirect.role,
            set_id=set_id,
            refs=[problem_id],
        )
        await session.commit()
        flash("Problem removed from the problem set.", FlashCategory.SUCCESS)
    except (ArenaProblemSetNotFoundError, ArenaProblemSetPermissionError) as exc:
        await session.rollback()
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    except ArenaProblemSetValidationError as exc:
        await session.rollback()
        flash(str(exc), FlashCategory.WARNING)
    return RedirectResponse(
        url=str(request.url_for("arena_class_problem_set_manage", class_id=class_id, set_id=set_id)),
        status_code=303,
    )


@router.post(
    "/classes/{class_id}/problem-sets/{set_id}/schedule",
    name="arena_class_problem_set_update_schedule",
)
async def class_problem_set_update_schedule(
    request: Request,
    class_id: str,
    set_id: str,
    flash: FlashDep,
    description: Annotated[str, Form()] = "",
    starts_on: Annotated[str, Form()] = "",
    deadline: Annotated[str, Form()] = "",
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Update the notes and schedule of a problem set."""
    user_or_redirect, _class_detail = await _require_problem_set_manager(
        request,
        current_user,
        class_id=class_id,
        session=session,
    )
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    try:
        await update_problem_set_details(
            session,
            actor_id=user_or_redirect.id,
            actor_role=user_or_redirect.role,
            set_id=set_id,
            description=description,
            starts_on=_parse_datetime_local(starts_on, user_or_redirect) if starts_on.strip() else None,
            deadline=_parse_datetime_local(deadline, user_or_redirect) if deadline.strip() else None,
            now=datetime.now(UTC),
        )
        await session.commit()
        flash("Problem set updated.", FlashCategory.SUCCESS)
    except (ArenaProblemSetNotFoundError, ArenaProblemSetPermissionError) as exc:
        await session.rollback()
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    except ArenaProblemSetValidationError as exc:
        await session.rollback()
        flash(str(exc), FlashCategory.WARNING)
    return RedirectResponse(
        url=str(request.url_for("arena_class_problem_set_manage", class_id=class_id, set_id=set_id)),
        status_code=303,
    )


@router.post(
    "/classes/{class_id}/problem-sets/{set_id}/stop-now",
    name="arena_class_problem_set_stop_now",
)
async def class_problem_set_stop_now(
    request: Request,
    class_id: str,
    set_id: str,
    flash: FlashDep,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Set the deadline to now, immediately closing the problem set."""
    user_or_redirect, _class_detail = await _require_problem_set_manager(
        request,
        current_user,
        class_id=class_id,
        session=session,
    )
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    try:
        await stop_problem_set_now(
            session,
            actor_id=user_or_redirect.id,
            actor_role=user_or_redirect.role,
            set_id=set_id,
            now=datetime.now(UTC),
        )
        await session.commit()
        flash("Problem set closed — no longer accepting submissions.", FlashCategory.SUCCESS)
    except ArenaProblemSetValidationError:
        await session.rollback()
        flash("Problem set is not currently accepting submissions.", FlashCategory.WARNING)
    except (ArenaProblemSetNotFoundError, ArenaProblemSetPermissionError) as exc:
        await session.rollback()
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    return RedirectResponse(
        url=str(request.url_for("arena_class_problem_set_manage", class_id=class_id, set_id=set_id)),
        status_code=303,
    )


@router.post(
    "/classes/{class_id}/problem-sets/{set_id}/delete",
    name="arena_class_problem_set_delete",
)
async def class_problem_set_delete(
    request: Request,
    class_id: str,
    set_id: str,
    flash: FlashDep,
    password: Annotated[str, Form()] = "",
    page: Annotated[str, Form()] = "1",
    sort: Annotated[str, Form()] = "deadline",
    direction: Annotated[str, Form()] = "desc",
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Delete a problem set after password confirmation."""
    user_or_redirect, _class_detail = await _require_problem_set_manager(
        request,
        current_user,
        class_id=class_id,
        session=session,
    )
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    if not user_or_redirect.check_password(password):
        flash("Incorrect password.", FlashCategory.DANGER)
        return RedirectResponse(
            url=_problem_set_list_url(request, class_id=class_id, page=page, sort=sort, direction=direction),
            status_code=303,
        )
    try:
        await arena_problem_set_service.delete_problem_set(
            session,
            actor_id=user_or_redirect.id,
            actor_role=user_or_redirect.role,
            set_id=set_id,
        )
        await session.commit()
        flash("Problem set deleted.", FlashCategory.SUCCESS)
    except (ArenaProblemSetNotFoundError, ArenaProblemSetPermissionError) as exc:
        await session.rollback()
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    return RedirectResponse(
        url=_problem_set_list_url(request, class_id=class_id, page=page, sort=sort, direction=direction),
        status_code=303,
    )
