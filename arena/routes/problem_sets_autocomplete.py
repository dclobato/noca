#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena teacher problem-set autocomplete route."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from arena.services import arena_class_detail_service, arena_problem_set_management_service
from arena.services.arena_class_service import ArenaClassNotFoundError
from arena.services.arena_problem_set_service import (
    ArenaProblemSetNotFoundError,
    ArenaProblemSetPermissionError,
)
from arena.services.session_service import build_current_next_url, build_login_redirect_response
from shared.enumerations import ArenaRole

router = APIRouter(tags=["arena-classes"])


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


@router.get(
    "/classes/{class_id}/problem-sets/{set_id}/problems/autocomplete",
    name="arena_class_problem_set_problem_autocomplete",
)
async def class_problem_set_problem_autocomplete(
    request: Request,
    class_id: str,
    set_id: str,
    q: Annotated[str, Query()] = "",
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return problem autocomplete matches for adding to a problem set."""
    user_or_redirect, _class_detail = await _require_problem_set_manager(
        request,
        current_user,
        class_id=class_id,
        session=session,
    )
    if isinstance(user_or_redirect, RedirectResponse):
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        rows = await arena_problem_set_management_service.search_set_candidate_problems(
            session,
            actor_id=user_or_redirect.id,
            actor_role=user_or_redirect.role,
            set_id=set_id,
            query=q,
        )
    except ArenaProblemSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Problem set not found") from exc
    except ArenaProblemSetPermissionError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    return JSONResponse(
        {
            "problems": [
                {
                    "id": row.problem_id,
                    "ref": str(row.arena_number),
                    "label": (
                        f"{row.arena_number} - {row.title} ({row.rating:.1f})"
                        if row.rating is not None
                        else f"{row.arena_number} - {row.title}"
                    ),
                }
                for row in rows
            ]
        }
    )
