#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena teacher problem-set report route."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.dependencies.export_rate_limit import arena_teacher_report_rate_limit
from arena.models.arena_users import ArenaUser
from arena.routes.class_route_guards import (
    html,
    problem_set_list_url,
    problem_set_report_url,
    require_problem_set_manager,
)
from arena.services import arena_problem_set_management_service
from arena.services.arena_problem_set_report_service import (
    StudentProblemGroup,
    get_student_problem_submissions_for_set,
)
from arena.services.arena_problem_set_service import (
    ArenaProblemSetNotFoundError,
    ArenaProblemSetPermissionError,
)
from shared.db_schema.arena import arena_users
from shared.enumerations import VERDICT_BADGE_CLASSES

router = APIRouter(tags=["arena-classes"])


@router.get(
    "/classes/{class_id}/problem-sets/{set_id}/report",
    response_class=HTMLResponse,
    name="arena_class_problem_set_report",
    dependencies=[Depends(arena_teacher_report_rate_limit)],
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
    user_or_redirect, class_detail = await require_problem_set_manager(
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
    return html(
        templates.TemplateResponse(
            request,
            "classes/problem_set_report.html",
            {
                "current_user": user_or_redirect,
                "class_detail": class_detail,
                "class_id": class_id,
                "report": report_data,
                "back_url": problem_set_list_url(
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
    dependencies=[Depends(arena_teacher_report_rate_limit)],
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
    user_or_redirect, class_detail = await require_problem_set_manager(
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
    return html(
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
                "back_url": problem_set_report_url(
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
