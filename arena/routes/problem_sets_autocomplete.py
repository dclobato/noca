#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena teacher problem-set autocomplete route."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from arena.routes.class_route_guards import require_problem_set_manager
from arena.services import arena_problem_set_management_service
from arena.services.arena_problem_set_service import (
    ArenaProblemSetNotFoundError,
    ArenaProblemSetPermissionError,
)

router = APIRouter(tags=["arena-classes"])


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
    user_or_redirect, _class_detail = await require_problem_set_manager(
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
