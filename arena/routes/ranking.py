#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena public ranking routes.

All routes are public — no authentication is required. ``current_user`` is
an optional dependency so the sidebar renders correctly for logged-in users.
"""

from typing import Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from arena.services import ranking_service
from arena.services.pagination_service import parse_page

router = APIRouter(prefix="/ranking", tags=["arena-ranking"])

_PER_PAGE = 50


def _html(response: Any) -> HTMLResponse:
    """Cast TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


@router.get("", response_class=HTMLResponse, name="arena_ranking_index")
async def ranking_index(
    request: Request,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
) -> Response:
    """Render the ranking landing page with choices for user and affiliation ranking."""
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "ranking/index.html",
            {"current_user": current_user},
        )
    )


@router.get("/users", response_class=HTMLResponse, name="arena_ranking_users")
async def ranking_users(
    request: Request,
    page: str | None = None,
    search: str | None = None,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the paginated user ranking."""
    pagination = await ranking_service.get_ranked_users_paginated(
        session,
        search=search or None,
        page=parse_page(page),
        per_page=_PER_PAGE,
    )
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "ranking/users.html",
            {
                "pagination": pagination,
                "search": search or "",
                "current_user": current_user,
            },
        )
    )


@router.get("/affiliations", response_class=HTMLResponse, name="arena_ranking_affiliations")
async def ranking_affiliations(
    request: Request,
    page: str | None = None,
    search: str | None = None,
    country_code: str | None = None,
    subdivision_code: str | None = None,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the paginated affiliation ranking."""
    clean_country = (country_code or "").strip().upper() or None
    clean_subdivision = (subdivision_code or "").strip().upper() or None if clean_country else None

    pagination = await ranking_service.get_ranked_affiliations_paginated(
        session,
        search=search or None,
        country_code=clean_country,
        subdivision_code=clean_subdivision,
        page=parse_page(page),
        per_page=_PER_PAGE,
    )
    countries, subdivisions = await ranking_service.get_affiliation_filter_options(session, country_code=clean_country)

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "ranking/affiliations.html",
            {
                "pagination": pagination,
                "search": search or "",
                "country_code": clean_country or "",
                "subdivision_code": clean_subdivision or "",
                "countries": countries,
                "subdivisions": subdivisions,
                "current_user": current_user,
            },
        )
    )


@router.get(
    "/affiliations/{affiliation_id}/users",
    response_class=HTMLResponse,
    name="arena_ranking_affiliation_users",
)
async def ranking_affiliation_users(
    request: Request,
    affiliation_id: str,
    page: str | None = None,
    search: str | None = None,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the user ranking scoped to a single affiliation."""
    affiliation = await ranking_service.get_affiliation_or_404(session, affiliation_id)
    pagination = await ranking_service.get_ranked_users_paginated(
        session,
        search=search or None,
        affiliation_id=affiliation_id,
        page=parse_page(page),
        per_page=_PER_PAGE,
    )
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "ranking/affiliation_users.html",
            {
                "pagination": pagination,
                "search": search or "",
                "affiliation": affiliation,
                "current_user": current_user,
            },
        )
    )
