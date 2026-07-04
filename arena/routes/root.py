#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena root routes.

Handles the public-facing dashboard at ``/`` and ``/dashboard``.
"""

import logging
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from arena.services.leaderboard_service import get_top_rated_users
from arena.services.problem_browse_service import get_random_problems
from shared.db_schema import languages as languages_table

logger = logging.getLogger(__name__)

router = APIRouter(tags=["arena-root"])


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


_FAVICON_DIR = Path(__file__).parent.parent / "static" / "favicon"

# Root-level favicon assets referenced by the favicon snippet in the base
# templates. Browsers and crawlers request these at the site root, so they are
# served here rather than under ``/static``.
FAVICON_ASSETS: dict[str, str] = {
    "favicon.ico": "image/x-icon",
    "favicon-16x16.png": "image/png",
    "favicon-32x32.png": "image/png",
    "favicon-48x48.png": "image/png",
    "favicon-96x96.png": "image/png",
    "favicon-180x180.png": "image/png",
    "favicon-192x192.png": "image/png",
    "favicon-512x512.png": "image/png",
    "mstile-150x150.png": "image/png",
    "site.webmanifest": "application/manifest+json",
    "browserconfig.xml": "application/xml",
}


def _serve_favicon_asset(asset: str) -> FileResponse:
    """Serve an allowlisted root-level favicon asset.

    Args:
        asset: File name relative to the favicon directory.

    Returns:
        The matching favicon file response.

    Raises:
        HTTPException: 404 when the requested asset is not allowlisted.
    """
    media_type = FAVICON_ASSETS.get(asset)
    if media_type is None:
        raise HTTPException(status_code=404)
    return FileResponse(
        _FAVICON_DIR / asset,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _register_favicon_routes() -> None:
    """Register one explicit root route per favicon asset.

    Explicit literal paths are used (rather than a catch-all) so these routes
    never shadow real application routes such as ``/dashboard``.
    """
    for asset in FAVICON_ASSETS:

        def _handler(asset: str = asset) -> FileResponse:
            return _serve_favicon_asset(asset)

        router.add_api_route(
            f"/{asset}",
            _handler,
            methods=["GET"],
            include_in_schema=False,
            name=f"arena_favicon_{asset.replace('.', '_').replace('-', '_')}",
        )


_register_favicon_routes()


@router.get("/", include_in_schema=False)
async def arena_root(request: Request) -> RedirectResponse:
    """Redirect the bare root to the dashboard."""
    return RedirectResponse(url=str(request.url_for("arena_dashboard")), status_code=302)


@router.get("/dashboard", response_class=HTMLResponse, name="arena_dashboard")
async def arena_dashboard(
    request: Request,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the Arena public dashboard.

    Displays a languages card (full-width) followed by two side-by-side cards:
    Random Problems and Top Users (7 rows each). Top Users is backed by Arena ratings.

    The ``current_user`` dependency validates the session cookie and performs
    the ``get_token_id()`` consistency check.  A mismatch (password change or
    force logout) raises ``ForceLogoutException`` before this handler runs,
    which the registered exception handler converts to a login-page redirect.

    Args:
        request: The current HTTP request.
        current_user: Authenticated ``ArenaUser`` or ``None`` for guests.
        session: Async database session used for dashboard summary data.
    """
    templates = request.app.state.arena_templates
    top_users = await get_top_rated_users(session, limit=7, only_users=False)
    random_problems = await get_random_problems(session, limit=7)
    result = await session.execute(
        select(languages_table).where(languages_table.c.active == True).order_by(languages_table.c.name)  # noqa: E712
    )
    languages = [dict(row) | {"devicon_name": row["icon"].split("-")[1]} for row in result.mappings().all()]
    return _html(
        templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "current_user": current_user,
                "top_users": top_users,
                "random_problems": random_problems,
                "languages": languages,
            },
        )
    )
