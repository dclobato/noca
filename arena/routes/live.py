#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena live submission feed (requires a logged-in user)."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.database import get_db
from arena.dependencies.auth import require_arena_user
from arena.models.arena_users import ArenaUser
from arena.services.live_feed_service import build_arena_live_feed_snapshot
from shared.enumerations import VERDICT_BADGE_CLASSES, VERDICT_LABELS
from shared.queue_schema import ArenaVerdictEvent
from shared.services.sse_refresh import iter_refresh_events

logger = logging.getLogger(__name__)

router = APIRouter(tags=["arena-live"])


def _html(response: Any) -> HTMLResponse:
    return cast(HTMLResponse, response)


@router.get("/live", response_class=HTMLResponse, name="arena_live")
async def arena_live_page(
    request: Request,
    current_user: ArenaUser = Depends(require_arena_user),
) -> HTMLResponse:
    """Render the Arena live submission feed page."""
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "live_feed.html",
            {
                "current_user": current_user,
                "live_feed_limit": settings.ARENA_LIVE_FEED_LIMIT,
            },
        )
    )


@router.get("/live/feed.json", name="arena_live_feed")
async def arena_live_feed_json(
    request: Request,
    current_user: ArenaUser = Depends(require_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return the latest finalized Arena submissions snapshot."""
    snapshot = await build_arena_live_feed_snapshot(session)
    return JSONResponse(
        {
            "live_feed_limit": snapshot.limit,
            "has_more": snapshot.has_more,
            "submissions": [
                {
                    "submission_id": row.submission_id,
                    "created_at": row.created_at.isoformat(),
                    "created_at_display": request.app.state.arena_templates.env.globals["arena_format_datetime"](
                        row.created_at,
                        current_user,
                        "%Y-%m-%d %H:%M:%S %Z",
                    ),
                    "affiliation_name": row.affiliation_name,
                    "affiliation_logo_url": (
                        str(
                            request.url_for(
                                "arena_affiliation_logo_thumbnail",
                                affiliation_id=row.affiliation_id,
                            )
                        )
                        if row.affiliation_id and row.affiliation_has_logo
                        else None
                    ),
                    "country_code": row.country_code,
                    "country_name": row.country_name,
                    "country_flag_url": (
                        str(
                            request.url_for(
                                "static_vendor",
                                path=f"img/flags/{row.country_code.lower()}.svg",
                            )
                        )
                        if row.country_code
                        else None
                    ),
                    "problem_number": row.problem_number,
                    "problem_title": row.problem_title,
                    "problem_url": str(request.url_for("arena_problem_detail", arena_number=row.problem_number)),
                    "language_name": row.language_name,
                    "language_icon": row.language_icon,
                    "language_icon_svg_url": str(
                        request.url_for(
                            "static_vendor",
                            path=f"img/devicon/{row.language_icon.split('-')[1]}-original.svg",
                        )
                    ),
                    "verdict": row.verdict,
                    "verdict_label": VERDICT_LABELS.get(row.verdict, row.verdict),
                    "verdict_badge_class": VERDICT_BADGE_CLASSES.get(row.verdict, "bg-secondary"),
                }
                for row in snapshot.rows
            ],
        }
    )


@router.get("/live/events", name="arena_live_events")
async def arena_live_events(request: Request) -> Response:
    """Stream lightweight refresh pings as new Arena verdicts finalize.

    No verdict data leaves the server here; the ``feed.json`` snapshot is the sole
    data source. This only tells the browser when to refetch.
    """

    async def _stream() -> AsyncIterator[str]:
        runtime = request.app.state.valkey_runtime
        async for chunk in iter_refresh_events(
            open_event_stream=lambda: cast(AsyncGenerator[ArenaVerdictEvent], runtime.iter_arena_verdict_events()),
            is_disconnected=request.is_disconnected,
            emit_initial_ping=True,
        ):
            yield chunk

    return StreamingResponse(_stream(), media_type="text/event-stream")
