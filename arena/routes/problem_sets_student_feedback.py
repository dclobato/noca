#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Teacher actions for overall feedback on one student's problem set."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.dependencies.export_rate_limit import arena_teacher_report_rate_limit
from arena.models.arena_problem_sets import ArenaProblemSet
from arena.models.arena_users import ArenaUser
from arena.routes.class_route_guards import require_problem_set_manager
from arena.routes.problem_sets_report import render_student_problem_set_report
from arena.services.arena_problem_set_feedback_service import (
    delete_problem_set_student_feedback,
    student_has_problem_set_submission,
    upsert_problem_set_student_feedback,
)
from shared.enumerations import ArenaNotificationKind
from shared.services.arena_notification_service import create_arena_notification

router = APIRouter(tags=["arena-classes"])


async def _feedback_target(session: AsyncSession, *, class_id: str, set_id: str, student_id: str) -> ArenaProblemSet:
    """Return an eligible set/student pair or raise a not-found response."""
    problem_set = await session.get(ArenaProblemSet, set_id)
    if problem_set is None or problem_set.class_id != class_id:
        raise HTTPException(status_code=404, detail="Problem set not found")
    if not await student_has_problem_set_submission(
        session,
        problem_set_id=set_id,
        student_id=student_id,
    ):
        raise HTTPException(status_code=404, detail="Student submission not found")
    return problem_set


def _student_report_url(
    request: Request,
    *,
    class_id: str,
    set_id: str,
    student_id: str,
    page: str,
    sort: str,
    direction: str,
) -> str:
    """Build the teacher student-report URL, retaining optional list context."""
    url = str(
        request.url_for(
            "arena_class_problem_set_report_student",
            class_id=class_id,
            set_id=set_id,
            user_id=student_id,
        )
    )
    params = {key: value for key, value in {"page": page, "sort": sort, "direction": direction}.items() if value}
    return f"{url}?{urlencode(params)}" if params else url


@router.post(
    "/classes/{class_id}/problem-sets/{set_id}/report/student/{user_id}/feedback",
    name="arena_class_problem_set_student_feedback_save",
    dependencies=[Depends(arena_teacher_report_rate_limit)],
)
async def save_problem_set_student_feedback(
    request: Request,
    class_id: str,
    set_id: str,
    user_id: str,
    flash: FlashDep,
    feedback: Annotated[str, Form()] = "",
    page: Annotated[str, Form()] = "",
    sort: Annotated[str, Form()] = "",
    direction: Annotated[str, Form()] = "",
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Create or replace a teacher's Markdown feedback for a student and set."""
    user_or_redirect, _class_detail = await require_problem_set_manager(
        request,
        current_user,
        class_id=class_id,
        session=session,
    )
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    problem_set = await _feedback_target(
        session,
        class_id=class_id,
        set_id=set_id,
        student_id=user_id,
    )
    redirect_url = _student_report_url(
        request,
        class_id=class_id,
        set_id=set_id,
        student_id=user_id,
        page=page,
        sort=sort,
        direction=direction,
    )
    try:
        feedback_at = await upsert_problem_set_student_feedback(
            session,
            problem_set_id=set_id,
            student_id=user_id,
            teacher_id=user_or_redirect.id,
            feedback_text=feedback,
        )
    except ValueError as exc:
        return await render_student_problem_set_report(
            request,
            class_id=class_id,
            set_id=set_id,
            user_id=user_id,
            page=page,
            sort=sort,
            direction=direction,
            current_user=user_or_redirect,
            session=session,
            feedback_draft=feedback,
            feedback_error=str(exc),
        )

    target_url = request.url_for(
        "arena_student_class_problem_set_detail",
        class_id=class_id,
        set_id=set_id,
    ).path
    await create_arena_notification(
        session,
        user_id=user_id,
        notification_kind=ArenaNotificationKind.PROBLEM_SET_FEEDBACK_POSTED,
        title="Problem set feedback",
        message=f"Your teacher left feedback on the problem set {problem_set.name}.",
        target_url=target_url,
        source_ref=f"{set_id}:{user_id}:{int(feedback_at.timestamp() * 1_000_000)}",
        context={"problem_set_id": set_id, "student_id": user_id},
    )
    await session.commit()
    flash("Problem set feedback saved.", FlashCategory.SUCCESS)
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post(
    "/classes/{class_id}/problem-sets/{set_id}/report/student/{user_id}/feedback/remove",
    name="arena_class_problem_set_student_feedback_remove",
    dependencies=[Depends(arena_teacher_report_rate_limit)],
)
async def remove_problem_set_student_feedback(
    request: Request,
    class_id: str,
    set_id: str,
    user_id: str,
    flash: FlashDep,
    page: Annotated[str, Form()] = "",
    sort: Annotated[str, Form()] = "",
    direction: Annotated[str, Form()] = "",
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Remove the overall feedback for a student and problem set."""
    user_or_redirect, _class_detail = await require_problem_set_manager(
        request,
        current_user,
        class_id=class_id,
        session=session,
    )
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    await _feedback_target(session, class_id=class_id, set_id=set_id, student_id=user_id)
    deleted = await delete_problem_set_student_feedback(
        session,
        problem_set_id=set_id,
        student_id=user_id,
    )
    await session.commit()
    flash(
        "Problem set feedback removed." if deleted else "No problem set feedback to remove.",
        FlashCategory.SUCCESS if deleted else FlashCategory.WARNING,
    )
    return RedirectResponse(
        url=_student_report_url(
            request,
            class_id=class_id,
            set_id=set_id,
            student_id=user_id,
            page=page,
            sort=sort,
            direction=direction,
        ),
        status_code=303,
    )
