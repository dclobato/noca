#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena teacher batch-feedback routes for one problem in a problem set."""

from __future__ import annotations

from datetime import date
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_problem_sets import ArenaProblemSet
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_users import ArenaUser
from arena.services import arena_batch_feedback_service, arena_class_detail_service
from arena.services.arena_class_service import ArenaClassNotFoundError
from arena.services.arena_problem_set_service import (
    ArenaProblemSetNotFoundError,
    ArenaProblemSetPermissionError,
)
from arena.services.arena_teacher_feedback_service import upsert_teacher_feedback
from arena.services.session_service import build_current_next_url, build_login_redirect_response
from shared.enumerations import ArenaNotificationKind, ArenaRole
from shared.services.arena_notification_service import create_arena_notification

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


async def _require_set_in_class(session: AsyncSession, *, class_id: str, set_id: str) -> None:
    """Raise 404 unless the problem set exists and belongs to the given class."""
    problem_set = await session.get(ArenaProblemSet, set_id)
    if problem_set is None or problem_set.class_id != class_id:
        raise HTTPException(status_code=404, detail="Problem set not found")


@router.get(
    "/classes/{class_id}/problem-sets/{set_id}/problems/{problem_id}/batch-feedback",
    response_class=HTMLResponse,
    name="arena_class_problem_set_batch_feedback",
)
async def class_problem_set_batch_feedback(
    request: Request,
    class_id: str,
    set_id: str,
    problem_id: str,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the teacher batch-feedback page for one problem in a problem set."""
    user_or_redirect, class_detail = await _require_problem_set_manager(
        request,
        current_user,
        class_id=class_id,
        session=session,
    )
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    await _require_set_in_class(session, class_id=class_id, set_id=set_id)
    try:
        data = await arena_batch_feedback_service.get_batch_feedback_data(
            session,
            actor_id=user_or_redirect.id,
            actor_role=user_or_redirect.role,
            set_id=set_id,
            problem_id=problem_id,
        )
    except ArenaProblemSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Problem set not found") from exc
    except ArenaProblemSetPermissionError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "classes/problem_set_batch_feedback.html",
            {
                "current_user": user_or_redirect,
                "class_detail": class_detail,
                "class_id": class_id,
                "set_id": set_id,
                "data": data,
            },
        )
    )


@router.post(
    "/classes/{class_id}/problem-sets/{set_id}/problems/{problem_id}/batch-feedback",
    name="arena_class_problem_set_batch_feedback_submit",
)
async def class_problem_set_batch_feedback_submit(
    request: Request,
    class_id: str,
    set_id: str,
    problem_id: str,
    flash: FlashDep,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Save teacher feedback for every changed submission in a batch."""
    user_or_redirect, _class_detail = await _require_problem_set_manager(
        request,
        current_user,
        class_id=class_id,
        session=session,
    )
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    await _require_set_in_class(session, class_id=class_id, set_id=set_id)

    redirect_url = str(
        request.url_for(
            "arena_class_problem_set_manage",
            class_id=class_id,
            set_id=set_id,
        )
    )

    form = await request.form()
    submitted: dict[str, str] = {}
    for key, value in form.multi_items():
        if key.startswith("feedback__") and isinstance(value, str) and value.strip():
            submitted[key.removeprefix("feedback__")] = value.strip()

    if not submitted:
        flash("No feedback was entered.", FlashCategory.WARNING)
        return RedirectResponse(url=redirect_url, status_code=303)

    try:
        valid = await arena_batch_feedback_service.validate_batch_submission_ids(
            session,
            actor_id=user_or_redirect.id,
            actor_role=user_or_redirect.role,
            set_id=set_id,
            problem_id=problem_id,
            submission_ids=list(submitted.keys()),
        )
    except ArenaProblemSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Problem set not found") from exc
    except ArenaProblemSetPermissionError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc

    problem = await session.get(ArenaProblem, problem_id)
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem not found")

    saved = 0
    unchanged = 0
    for submission_id, (student_id, existing_text) in valid.items():
        new_text = submitted[submission_id]
        if existing_text is not None and new_text == existing_text:
            unchanged += 1
            continue
        feedback_at = await upsert_teacher_feedback(
            session,
            submission_id=submission_id,
            teacher_id=user_or_redirect.id,
            feedback_text=new_text,
        )
        await create_arena_notification(
            session,
            user_id=student_id,
            notification_kind=ArenaNotificationKind.TEACHER_FEEDBACK_POSTED,
            title="Teacher feedback",
            message=(
                f"Your teacher left feedback on your submission for problem {problem.arena_number} - {problem.title}."
            ),
            target_url=f"/submissions/{submission_id}",
            source_ref=f"{submission_id}:{int(feedback_at.timestamp() * 1_000_000)}",
            context={"submission_id": submission_id},
        )
        saved += 1

    dropped = len(submitted) - len(valid)
    await session.commit()

    if saved:
        flash(f"Saved feedback for {saved} submission{'s' if saved != 1 else ''}.", FlashCategory.SUCCESS)
    if unchanged or dropped:
        notes = []
        if unchanged:
            notes.append(f"{unchanged} unchanged")
        if dropped:
            notes.append(f"{dropped} no longer eligible")
        flash(f"Skipped: {', '.join(notes)}.", FlashCategory.WARNING)
    if not saved and not unchanged and not dropped:
        flash("No feedback was saved.", FlashCategory.WARNING)

    return RedirectResponse(url=redirect_url, status_code=303)
