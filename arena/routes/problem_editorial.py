#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public Arena route for viewing a problem's editorial.

Routes:
  GET /problems/{arena_number}/editorial    arena_problem_editorial_view
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import require_arena_user
from arena.models.arena_users import ArenaUser
from arena.services import problem_browse_service
from shared.enumerations import ArenaEditorialReleasePolicy
from shared.http_params import DbId

router = APIRouter(tags=["arena-problems"])


@router.get(
    "/problems/{arena_number:dbid}/editorial",
    response_class=HTMLResponse,
    name="arena_problem_editorial_view",
)
async def arena_problem_editorial_view(
    request: Request,
    arena_number: DbId,
    current_user: ArenaUser = Depends(require_arena_user),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render a problem's editorial as a standalone Markdown page.

    The release-policy gate is re-checked here in full, independent of
    whether the problem detail page happened to show a link: a direct GET
    must not be able to bypass the gate.
    """
    result = await problem_browse_service.get_enabled_problem_by_number(session, arena_number)
    if result is None:
        raise HTTPException(status_code=404, detail="Problem not found")
    problem, _author_info = result

    if not problem.editorial or problem.editorial_release_policy is ArenaEditorialReleasePolicy.NEVER:
        raise HTTPException(status_code=404, detail="This problem has no editorial.")

    if problem.editorial_release_policy is ArenaEditorialReleasePolicy.AFTER_AC:
        solved_at, _tried_at, _is_favorite = await problem_browse_service.get_user_problem_status(
            session, user_id=current_user.id, problem_id=problem.id
        )
        if solved_at is None:
            raise HTTPException(status_code=404, detail="This problem has no editorial.")

    return HTMLResponse(
        request.app.state.arena_templates.get_template("problems/editorial_view.html").render(
            request=request,
            current_user=current_user,
            problem=problem,
            problem_label=str(problem.arena_number),
            editorial_content=problem.editorial,
        )
    )
