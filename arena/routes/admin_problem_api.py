#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""JSON API routes for the Arena admin problem management UI.

Provides:
  - Rating history endpoint for the ECharts spikeline on the problem edit form.
  - Category search endpoint for the category autocomplete tag picker.
  - Source, free-text author, and license suggestion endpoint for the problem form.
  - Statement-language detection endpoint used by the problem form's
    confirmation modal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.admin import require_arena_problem_editor
from arena.models.arena_users import ArenaUser
from arena.services import admin_problem_service
from arena.services.problem_search_service import ProblemSuggestionField
from arena.services.statement_language_service import detect_statement_language_async
from arena.services.user_timezone_service import format_user_datetime
from shared.db_schema.arena.arena_rating_history import arena_problem_rating_history
from shared.enumerations import ArenaRole

router = APIRouter(prefix="/admin", tags=["arena-admin-api"])


@router.get("/problems/categories/search", name="arena_admin_problem_categories_search")
async def admin_problem_categories_search(
    q: str = "",
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return categories matching the search query for the autocomplete picker.

    Args:
        q: Substring to search (case-insensitive). Empty returns top 15.

    Returns:
        JSONResponse: ``{"categories": [{id, name, color, foreground_color}, ...]}``
    """
    categories = await admin_problem_service.search_categories(session, query=q)
    return JSONResponse(
        {
            "categories": [
                {
                    "id": cat.id,
                    "name": cat.name,
                    "color": cat.color,
                    "foreground_color": cat.foreground_color,
                }
                for cat in categories
            ]
        }
    )


@router.get("/problems/suggestions", name="arena_admin_problem_suggestions")
async def arena_admin_problem_suggestions(
    field: Annotated[ProblemSuggestionField, Query()],
    q: Annotated[str, Query(min_length=2, max_length=256)],
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return visible author, source, or license suggestions for the problem form.

    Editors receive enabled problems plus their own disabled drafts; admins receive all.

    Args:
        field: Stored text field to search: ``"author"``, ``"license"`, or
            ``"source"``.
        q: Literal search text, between 2 and 256 characters.
        current_user: Authenticated admin or problem editor.
        session: Active request database session.

    Returns:
        JSONResponse: ``{"suggestions": ["stored value", ...]}`` with at most 15 strings.
    """
    suggestions = await admin_problem_service.search_problem_suggestions(
        session,
        field=field,
        query=q,
        caller_id=current_user.id,
        is_admin=current_user.role == ArenaRole.ARENA_ADMIN,
    )
    return JSONResponse({"suggestions": suggestions})


class DetectLanguageRequest(BaseModel):
    """Body of a statement-language detection request."""

    statement: str = ""
    title: str = ""


@router.post("/problems/detect-language", name="arena_admin_problem_detect_language")
async def admin_problem_detect_language(
    payload: DetectLanguageRequest,
    current_user: ArenaUser = Depends(require_arena_problem_editor),
) -> JSONResponse:
    """Detect the natural language of a statement being edited.

    Used by the problem form to warn about a mismatch *before* submitting, so a
    disagreement does not cost the author their pending file uploads. The save
    routes run the same detection server-side, which stays authoritative.

    Args:
        payload: The statement and title currently in the form.
        current_user: The authenticated problem editor.

    Returns:
        JSONResponse: ``{"language": "pt" | "en" | "es" | null}``
    """
    del current_user
    detected = await detect_statement_language_async(payload.statement, title=payload.title)
    return JSONResponse({"language": detected.value if detected else None})


@router.get(
    "/problems/{problem_id}/rating-history",
    name="arena_admin_problem_rating_history",
)
async def admin_problem_rating_history(
    problem_id: str,
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return the rating history for a problem for the last 24 months.

    Access-controlled: judges may only query their own problems.

    Args:
        problem_id: UUID of the Arena problem.

    Returns:
        JSONResponse: ``{"history": [{"ts": "<ISO8601>", "rating": <float>}, ...]}``
    """
    is_admin = current_user.role == ArenaRole.ARENA_ADMIN
    problem = await admin_problem_service.get_problem(
        session,
        problem_id,
        caller_id=current_user.id,
        is_admin=is_admin,
    )
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem not found")

    cutoff = datetime.now(tz=UTC) - timedelta(days=730)
    stmt = (
        select(
            arena_problem_rating_history.c.computed_at,
            arena_problem_rating_history.c.rating,
        )
        .where(
            arena_problem_rating_history.c.problem_id == problem_id,
            arena_problem_rating_history.c.computed_at >= cutoff,
        )
        .order_by(arena_problem_rating_history.c.computed_at.asc())
    )
    rows = (await session.execute(stmt)).fetchall()
    return JSONResponse(
        {
            "history": [
                {
                    "ts": row.computed_at.isoformat(),
                    "ts_display": format_user_datetime(row.computed_at, current_user, "%Y-%m-%d"),
                    "rating": row.rating / 10.0,
                }
                for row in rows
            ]
        }
    )
