#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena user public-profile page and its JSON data endpoints.

All four routes require a logged-in viewer (enforced by the global
access-control gate).  Visibility of the target user is gated by the
``public_profile`` and ``ranking_visible`` flags; ``ArenaRole.ARENA_ADMIN``
viewers bypass the flag check for moderation.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from arena.database import get_db
from arena.dependencies.auth import require_arena_user
from arena.models.arena_badges import ArenaUserBadge
from arena.models.arena_users import ArenaUser
from arena.services.user_stats_service import get_user_statistics
from arena.services.user_timezone_service import format_user_datetime
from shared.db_schema.arena.arena_heatmap import arena_user_submission_heatmap as _user_heatmap
from shared.db_schema.arena.arena_rating_history import arena_user_rating_history as _user_rating_history
from shared.enumerations import ARENA_BADGE_METADATA, ArenaRole

router = APIRouter(tags=["arena-users"])

_STATS_CACHE_CONTROL = "public, max-age=300"


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def _can_view_public_profile(profile_user: ArenaUser, viewer: ArenaUser) -> bool:
    """Return whether ``viewer`` is allowed to see ``profile_user``'s public page.

    Args:
        profile_user: The user whose public profile is being requested.
        viewer: The authenticated Arena user requesting the profile.

    Returns:
        bool: True if access should be granted.
    """
    if viewer.role == ArenaRole.ARENA_ADMIN:
        return True
    return bool(profile_user.ativo and profile_user.public_profile and profile_user.ranking_visible)


def _require_public_profile(
    profile_user: ArenaUser,
    viewer: ArenaUser,
) -> None:
    """Raise HTTP 404 if ``viewer`` cannot see ``profile_user``'s public page.

    Args:
        profile_user: The user whose public profile is being requested.
        viewer: The authenticated Arena user requesting the profile.

    Raises:
        HTTPException: 404 when the profile is not publicly accessible.
    """
    if not _can_view_public_profile(profile_user, viewer):
        raise HTTPException(status_code=404, detail="Profile not found")


@router.get("/profile/{user_id}", response_class=HTMLResponse, name="arena_user_profile_public")
async def arena_user_profile_public(
    user_id: str,
    request: Request,
    current_user: ArenaUser = Depends(require_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the public profile page for an Arena user.

    Args:
        user_id: UUID of the Arena user whose profile to render.
        request: Current HTTP request.
        current_user: The authenticated Arena user (login required).
        session: Active database session.

    Returns:
        HTMLResponse | RedirectResponse: Rendered public profile, or 404 when
        the user has not opted in to a public profile.
    """
    result = await session.execute(
        select(ArenaUser).options(selectinload(ArenaUser.affiliation)).where(ArenaUser.id == user_id)
    )
    profile_user = result.scalar_one_or_none()
    if profile_user is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    _require_public_profile(profile_user, current_user)

    badge_result = await session.scalars(
        select(ArenaUserBadge)
        .where(ArenaUserBadge.user_id == profile_user.id)
        .order_by(ArenaUserBadge.awarded_at.desc(), ArenaUserBadge.id.desc())
    )
    badges = [badge for badge in badge_result.all() if badge.badge.value in ARENA_BADGE_METADATA]

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "users/public_profile.html",
            {
                "current_user": current_user,
                "profile_user": profile_user,
                "badges": badges,
                "badge_metadata": ARENA_BADGE_METADATA,
                "rating_history_url": str(
                    request.url_for("arena_user_profile_rating_history_public", user_id=profile_user.id)
                ),
                "heatmap_url": str(
                    request.url_for("arena_user_profile_submission_heatmap_public", user_id=profile_user.id)
                ),
                "statistics_url": str(request.url_for("arena_user_profile_statistics_public", user_id=profile_user.id)),
            },
        )
    )


@router.get(
    "/profile/{user_id}/rating-history.json",
    name="arena_user_profile_rating_history_public",
)
async def arena_user_profile_rating_history_public(
    user_id: str,
    request: Request,
    current_user: ArenaUser = Depends(require_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return the user's rating history for the last 24 months.

    Args:
        user_id: UUID of the Arena user.
        request: Current HTTP request.
        current_user: The authenticated Arena user (login required).
        session: Active database session.

    Returns:
        JSONResponse: ``{"history": [{"ts", "ts_display", "rating"}]}`` for the
        last 730 days. Empty list when the user has no history.
    """
    profile_user = await session.get(ArenaUser, user_id)
    if profile_user is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    _require_public_profile(profile_user, current_user)

    cutoff = datetime.now(tz=UTC) - timedelta(days=730)
    stmt = (
        select(_user_rating_history.c.computed_at, _user_rating_history.c.rating)
        .where(
            _user_rating_history.c.user_id == profile_user.id,
            _user_rating_history.c.computed_at >= cutoff,
        )
        .order_by(_user_rating_history.c.computed_at.asc())
    )
    rows = (await session.execute(stmt)).fetchall()
    return JSONResponse(
        {
            "history": [
                {
                    "ts": row.computed_at.isoformat(),
                    "ts_display": format_user_datetime(row.computed_at, profile_user, "%Y-%m-%d"),
                    "rating": row.rating,
                }
                for row in rows
            ]
        },
        headers={"Cache-Control": _STATS_CACHE_CONTROL},
    )


@router.get(
    "/profile/{user_id}/submission-heatmap.json",
    name="arena_user_profile_submission_heatmap_public",
)
async def arena_user_profile_submission_heatmap_public(
    user_id: str,
    request: Request,
    current_user: ArenaUser = Depends(require_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return the precomputed submission heatmap for the last 52 weeks.

    Args:
        user_id: UUID of the Arena user.
        request: Current HTTP request.
        current_user: The authenticated Arena user (login required).
        session: Active database session.

    Returns:
        JSONResponse: ``{"heatmap", "range_start", "range_end", "computed_at"}``.
    """
    profile_user = await session.get(ArenaUser, user_id)
    if profile_user is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    _require_public_profile(profile_user, current_user)

    row = (
        (
            await session.execute(
                select(
                    _user_heatmap.c.data,
                    _user_heatmap.c.range_start,
                    _user_heatmap.c.range_end,
                    _user_heatmap.c.computed_at,
                ).where(_user_heatmap.c.user_id == profile_user.id)
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        today = datetime.now(tz=UTC).date()
        start = (today - timedelta(days=363)).isoformat()
        end = today.isoformat()
        return JSONResponse(
            {"heatmap": [], "range_start": start, "range_end": end, "computed_at": None},
            headers={"Cache-Control": _STATS_CACHE_CONTROL},
        )
    return JSONResponse(
        {
            "heatmap": row["data"],
            "range_start": row["range_start"],
            "range_end": row["range_end"],
            "computed_at": row["computed_at"].isoformat() if row["computed_at"] else None,
        },
        headers={"Cache-Control": _STATS_CACHE_CONTROL},
    )


@router.get(
    "/profile/{user_id}/statistics.json",
    name="arena_user_profile_statistics_public",
)
async def arena_user_profile_statistics_public(
    user_id: str,
    request: Request,
    current_user: ArenaUser = Depends(require_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return the precomputed verdict and language distributions for a user.

    Args:
        user_id: UUID of the Arena user.
        request: Current HTTP request.
        current_user: The authenticated Arena user (login required).
        session: Active database session.

    Returns:
        JSONResponse: ``{verdicts, languages, total_submissions, computed_at}``,
        or an empty payload when statistics have not been computed yet.
    """
    profile_user = await session.get(ArenaUser, user_id)
    if profile_user is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    _require_public_profile(profile_user, current_user)

    payload = await get_user_statistics(session, profile_user.id)
    if payload is None:
        return JSONResponse(
            {"total_submissions": 0, "verdicts": [], "languages": [], "computed_at": None},
            headers={"Cache-Control": _STATS_CACHE_CONTROL},
        )
    return JSONResponse(payload, headers={"Cache-Control": _STATS_CACHE_CONTROL})
