#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
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
from arena.services.arena_problem_set_feedback_service import (
    get_problem_set_student_feedback,
    student_has_problem_set_submission,
)
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


async def render_student_problem_set_report(
    request: Request,
    class_id: str,
    set_id: str,
    user_id: str,
    page: str | None,
    sort: str | None,
    direction: str | None,
    current_user: ArenaUser | None,
    session: AsyncSession,
    feedback_draft: str | None = None,
    feedback_error: str | None = None,
) -> Response:
    """Render the teacher's submission drill-down and overall-feedback editor.

    A validation refusal receives this renderer with the submitted Markdown,
    avoiding a redirect that would discard a teacher's draft.
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

    feedback = await get_problem_set_student_feedback(
        session,
        problem_set_id=set_id,
        student_id=user_id,
    )
    can_leave_feedback = await student_has_problem_set_submission(
        session,
        problem_set_id=set_id,
        student_id=user_id,
    )

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
                "feedback": feedback,
                "can_leave_feedback": can_leave_feedback,
                "feedback_draft": feedback_draft,
                "feedback_error": feedback_error,
                "page": page or "",
                "sort": sort or "",
                "direction": direction or "",
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
    """Render all submissions by one student for the problems in a problem set."""
    return await render_student_problem_set_report(
        request,
        class_id=class_id,
        set_id=set_id,
        user_id=user_id,
        page=page,
        sort=sort,
        direction=direction,
        current_user=current_user,
        session=session,
    )
