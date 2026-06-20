#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena teacher problem-set report route."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, cast
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from arena.services import arena_class_detail_service, arena_problem_set_management_service
from arena.services.arena_class_service import ArenaClassNotFoundError
from arena.services.arena_problem_set_report_service import (
    StudentProblemGroup,
    get_student_problem_submissions_for_set,
)
from arena.services.arena_problem_set_service import (
    ArenaProblemSetNotFoundError,
    ArenaProblemSetPermissionError,
)
from arena.services.session_service import build_current_next_url, build_login_redirect_response
from shared.db_schema.arena import arena_users
from shared.enumerations import VERDICT_BADGE_CLASSES, ArenaRole

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


def _problem_set_report_url(
    request: Request,
    *,
    class_id: str,
    set_id: str,
    page: int | str | None = None,
    sort: str | None = None,
    direction: str | None = None,
) -> str:
    """Build the problem-set report URL with optional pagination/sort context."""
    params = {
        key: str(value)
        for key, value in {
            "page": page,
            "sort": sort,
            "direction": direction,
        }.items()
        if value not in {None, ""}
    }
    url = str(request.url_for("arena_class_problem_set_report", class_id=class_id, set_id=set_id))
    return f"{url}?{urlencode(params)}" if params else url


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


@router.get(
    "/classes/{class_id}/problem-sets/{set_id}/report",
    response_class=HTMLResponse,
    name="arena_class_problem_set_report",
)
async def class_problem_set_report(
    request: Request,
    class_id: str,
    set_id: str,
    page: str | None = None,
    sort: str | None = None,
    direction: str | None = None,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the teacher problem-set report page."""
    user_or_redirect, class_detail = await _require_problem_set_manager(
        request,
        current_user,
        class_id=class_id,
        session=session,
    )
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    try:
        report_data = await arena_problem_set_management_service.build_teacher_problem_set_report(
            session,
            actor_id=user_or_redirect.id,
            actor_role=user_or_redirect.role,
            set_id=set_id,
            now=datetime.now(UTC),
        )
    except ArenaProblemSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Problem set not found") from exc
    except ArenaProblemSetPermissionError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "classes/problem_set_report.html",
            {
                "current_user": user_or_redirect,
                "class_detail": class_detail,
                "class_id": class_id,
                "report": report_data,
                "back_url": _problem_set_list_url(
                    request,
                    class_id=class_id,
                    page=page,
                    sort=sort,
                    direction=direction,
                ),
                "verdict_badge_classes": VERDICT_BADGE_CLASSES,
            },
        )
    )


@router.get(
    "/classes/{class_id}/problem-sets/{set_id}/report/student/{user_id}",
    response_class=HTMLResponse,
    name="arena_class_problem_set_report_student",
)
async def class_problem_set_report_student(
    request: Request,
    class_id: str,
    set_id: str,
    user_id: str,
    page: str | None = None,
    sort: str | None = None,
    direction: str | None = None,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render all submissions by one student for the problems in a problem set.

    Accessible to the class teacher and ARENA_ADMINs.  Submissions are grouped
    by problem (accordion) and ordered newest-first within each group.

    Args:
        request: Current HTTP request.
        class_id: UUID of the ``arena_classes`` row.
        set_id: UUID of the ``arena_problem_sets`` row.
        user_id: UUID of the ``arena_users`` row for the student.
        page: Optional pagination context forwarded from the list page.
        sort: Optional sort context forwarded from the list page.
        direction: Optional sort direction forwarded from the list page.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.

    Returns:
        HTMLResponse: Student submissions accordion page, or redirect on auth failure.

    Raises:
        HTTPException: 403 when the caller is not a teacher/admin, 404 when the
            student or problem set is not found.
    """
    user_or_redirect, class_detail = await _require_problem_set_manager(
        request,
        current_user,
        class_id=class_id,
        session=session,
    )
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect

    student_name = await session.scalar(select(arena_users.c.nome).where(arena_users.c.id == user_id))
    if student_name is None:
        raise HTTPException(status_code=404, detail="Student not found")

    try:
        groups: tuple[StudentProblemGroup, ...] = await get_student_problem_submissions_for_set(
            session,
            actor_id=user_or_redirect.id,
            actor_role=user_or_redirect.role,
            set_id=set_id,
            user_id=user_id,
        )
    except ArenaProblemSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Problem set not found") from exc
    except ArenaProblemSetPermissionError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "classes/problem_set_student_submissions.html",
            {
                "current_user": user_or_redirect,
                "class_detail": class_detail,
                "class_id": class_id,
                "set_id": set_id,
                "user_id": user_id,
                "student_name": student_name,
                "groups": groups,
                "back_url": _problem_set_report_url(
                    request,
                    class_id=class_id,
                    set_id=set_id,
                    page=page,
                    sort=sort,
                    direction=direction,
                ),
                "verdict_badge_classes": VERDICT_BADGE_CLASSES,
            },
        )
    )
