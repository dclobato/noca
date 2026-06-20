#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Student-facing problem-set detail route."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from arena.services import arena_student_problem_set_service
from arena.services.arena_problem_set_service import (
    ArenaProblemSetNotFoundError,
    ArenaProblemSetPermissionError,
)
from arena.services.session_service import build_current_next_url, build_login_redirect_response
from shared.enumerations import VERDICT_BADGE_CLASSES, VERDICT_LABELS, VERDICT_PRIORITY

router = APIRouter(tags=["arena-classes"])


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def _require_user(request: Request, current_user: ArenaUser | None) -> ArenaUser | RedirectResponse:
    """Return the current user or a login redirect response."""
    if current_user is None:
        return build_login_redirect_response(request, next_url=build_current_next_url(request))
    return current_user


@router.get(
    "/classes/{class_id}/my-problem-sets/{set_id}",
    response_class=HTMLResponse,
    name="arena_student_class_problem_set_detail",
)
async def student_class_problem_set_detail(
    request: Request,
    class_id: str,
    set_id: str,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the student problem-set detail page.

    Shows the problem set header, a table of problems with the student's best
    verdict per problem, and — after the deadline passes — a footer with the
    student's snapshot rating and a verdict distribution table.
    """
    user_or_redirect = _require_user(request, current_user)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    user = user_or_redirect

    try:
        detail = await arena_student_problem_set_service.get_student_problem_set_detail(
            session,
            actor_id=user.id,
            actor_role=user.role,
            set_id=set_id,
            now=datetime.now(UTC),
        )
    except ArenaProblemSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Problem set not found") from exc
    except ArenaProblemSetPermissionError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc

    if detail.class_id != class_id:
        raise HTTPException(status_code=404, detail="Problem set not found")

    back_url = str(request.url_for("arena_class_detail", class_id=class_id))

    # Build verdict distribution for the footer (AC-first order, plus "No submission" column)
    verdict_counts: dict[str, int] = {}
    no_submission_count = 0
    for row in detail.problems:
        if row.best_verdict is None:
            no_submission_count += 1
        else:
            verdict_counts[row.best_verdict] = verdict_counts.get(row.best_verdict, 0) + 1

    # Follow VERDICT_PRIORITY reversed so AC appears first
    verdict_summary = [
        {
            "verdict": v.value,
            "label": VERDICT_LABELS[v.value],
            "count": verdict_counts.get(v.value, 0),
        }
        for v in reversed(VERDICT_PRIORITY)
    ]

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "classes/student_problem_set_detail.html",
            {
                "current_user": user,
                "detail": detail,
                "back_url": back_url,
                "verdict_badge_classes": VERDICT_BADGE_CLASSES,
                "verdict_summary": verdict_summary,
                "no_submission_count": no_submission_count,
            },
        )
    )
