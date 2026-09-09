#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public Arena collection index.

A collection is an event (ICPC, Maratona SBC, InterIF) or a class (Iniciantes,
Expressoes regulares). This page is the second way into the catalogue: pick a
collection, then keep filtering by category inside it. The scoped listing is the
ordinary ``/problems`` page with a ``collection`` query parameter, so search,
sort, language and pagination are not reimplemented here.

Lives in its own module rather than in ``problems.py``, which is already past
the repository's file-size guidance.
"""

from typing import Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from arena.services import problem_browse_service

router = APIRouter(tags=["arena-problems"])


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


@router.get("/collections", response_class=HTMLResponse, name="arena_collection_index")
async def arena_collection_index(
    request: Request,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the public collection index.

    Shows one card per collection with its enabled-problem count, plus an "All
    collections" card leading to the unscoped catalogue. Public, like the
    problem list it leads into: it renders for guests and logged-in users alike.

    Args:
        request: The current HTTP request.
        current_user: Authenticated ``ArenaUser`` or ``None`` for guests.
        session: Async database session.

    Returns:
        HTMLResponse: The rendered collection index page.
    """
    collections = await problem_browse_service.list_collections_with_counts(session)
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "problems/collection_index.html",
            {
                "current_user": current_user,
                "collections": collections,
            },
        )
    )
