#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public Animator contest index."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from animator.dependencies import DbSession
from animator.services.contest_index_service import list_animator_contests

router = APIRouter(tags=["animator-index"])


@router.get("/", response_class=HTMLResponse, name="animator_index")
async def animator_index(request: Request, db: DbSession) -> Response:
    """Render enabled, non-archived contests grouped by lifecycle."""
    contest_groups = await list_animator_contests(db)
    return request.app.state.templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "animator_index.html",
        {"contest_groups": contest_groups},
    )
