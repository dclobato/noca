#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public Arena affiliation routes.

Provides unauthenticated access to affiliation logo images and rating
history so that user-facing and admin views can render logos and charts
via standard ``<img src="...">`` and fetch calls.
"""

from base64 import b64decode
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_affiliations import ArenaAffiliation
from arena.models.arena_users import ArenaUser
from arena.services.user_timezone_service import format_user_datetime
from shared.db_schema.arena.arena_rating_history import (
    arena_affiliation_rating_history as affiliation_rating_history_table,
)
from shared.services.imageprocessing_service import ImageProcessingService

router = APIRouter(tags=["arena-affiliations"])


def _get_image_service(request: Request) -> ImageProcessingService:
    """Return the app-scoped image processing service."""
    return request.app.state.image_service  # type: ignore[no-any-return]


@router.get("/affiliations/{affiliation_id}/logo", name="arena_affiliation_logo")
async def arena_affiliation_logo(
    affiliation_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Return the stored affiliation logo image.

    No authentication required.  Returns 404 when the affiliation does not
    exist or has no logo stored.

    Args:
        affiliation_id: Affiliation UUID primary key.
        request: Current HTTP request (provides the image service).
        session: Active database session.

    Returns:
        Response: Image bytes with the stored MIME type.

    Raises:
        HTTPException: 404 if the affiliation is not found or has no logo.
    """
    affiliation = await session.get(ArenaAffiliation, affiliation_id)
    if affiliation is None:
        raise HTTPException(status_code=404, detail="Affiliation not found")
    if not affiliation.logo_base64 or not affiliation.logo_mime:
        raise HTTPException(status_code=404, detail="No logo available")
    data = b64decode(str(affiliation.logo_base64))
    image_service = _get_image_service(request)
    return image_service.build_image_response(data, affiliation.logo_mime)


@router.get("/affiliations/{affiliation_id}/logo/thumbnail", name="arena_affiliation_logo_thumbnail")
async def arena_affiliation_logo_thumbnail(
    affiliation_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Return the stored affiliation logo thumbnail image.

    No authentication required.  If the thumbnail is not yet generated
    (pre-existing logos), redirects to the full-size logo route as a
    graceful fallback.  Returns 404 when the affiliation does not exist
    or has no logo stored at all.

    Args:
        affiliation_id: Affiliation UUID primary key.
        request: Current HTTP request (provides the image service).
        session: Active database session.

    Returns:
        Response: Thumbnail image bytes, or a redirect to the full logo.

    Raises:
        HTTPException: 404 if the affiliation is not found or has no logo.
    """
    affiliation = await session.get(ArenaAffiliation, affiliation_id)
    if affiliation is None:
        raise HTTPException(status_code=404, detail="Affiliation not found")
    if not affiliation.logo_base64 or not affiliation.logo_mime:
        raise HTTPException(status_code=404, detail="No logo available")
    thumbnail = affiliation.logo_thumbnail
    if thumbnail is not None:
        data, mime = thumbnail
        image_service = _get_image_service(request)
        return image_service.build_image_response(data, mime)
    full_url = str(request.url_for("arena_affiliation_logo", affiliation_id=affiliation_id))
    return RedirectResponse(url=full_url, status_code=302)


@router.get("/affiliations/{affiliation_id}/rating-history", name="arena_affiliation_rating_history")
async def arena_affiliation_rating_history(
    affiliation_id: str,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return the affiliation's rating history for the last 24 months.

    No authentication required.  Returns 404 when the affiliation does not
    exist.  Returns an empty history list when no rating records are found.

    Args:
        affiliation_id: Affiliation UUID primary key.
        session: Active database session.

    Returns:
        JSONResponse: ``{"history": [{"ts": ISO8601, "rating": int}, ...]}``

    Raises:
        HTTPException: 404 if the affiliation is not found.
    """
    affiliation = await session.get(ArenaAffiliation, affiliation_id)
    if affiliation is None:
        raise HTTPException(status_code=404, detail="Affiliation not found")
    cutoff = datetime.now(tz=UTC) - timedelta(days=730)
    stmt = (
        select(
            affiliation_rating_history_table.c.computed_at,
            affiliation_rating_history_table.c.rating,
        )
        .where(
            affiliation_rating_history_table.c.affiliation_id == affiliation_id,
            affiliation_rating_history_table.c.computed_at >= cutoff,
        )
        .order_by(affiliation_rating_history_table.c.computed_at.asc())
    )
    rows = (await session.execute(stmt)).fetchall()
    return JSONResponse(
        {
            "history": [
                {
                    "ts": row.computed_at.isoformat(),
                    "ts_display": format_user_datetime(row.computed_at, current_user, "%Y-%m-%d"),
                    "rating": row.rating,
                }
                for row in rows
            ]
        }
    )
