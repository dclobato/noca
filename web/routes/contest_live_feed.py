#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public live submission feed for a contest (no authentication required)."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from typing import cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from shared.queue_schema import VerdictEvent
from shared.services.sse_refresh import iter_refresh_events
from web.database import get_db
from web.models.contest import Contest
from web.services.contest_service import get_contest_by_slug
from web.services.live_feed_service import (
    CONTEST_LIVE_FEED_LIMIT,
    build_contest_live_feed_snapshot,
)
from web.services.public_rate_limits import enforce_live_feed_rate_limit
from web.services.sse_limits import enforce_live_events_slots

router = APIRouter(prefix="/c/{slug}/live", tags=["contest_live"])


@router.get("", response_class=HTMLResponse, name="contest_live")
async def live_feed_page(
    request: Request,
    contest: Contest = Depends(get_contest_by_slug),
) -> HTMLResponse:
    """Render the public live submission feed page."""
    templates = request.app.state.templates
    return cast(
        HTMLResponse,
        templates.TemplateResponse(
            request,
            "contest/live_feed.html",
            {
                "current_user": None,
                "contest": contest,
                "not_started": contest.upcoming,
                "live_feed_limit": CONTEST_LIVE_FEED_LIMIT,
            },
        ),
    )


FEED_CACHE_CONTROL = "public, max-age=5"
"""The snapshot is identical for every viewer (blackout masking is server-side),
so a shared cache may reuse it briefly while a debounced refetch burst hits."""


@router.get("/feed.json", name="contest_live_feed", dependencies=[Depends(enforce_live_feed_rate_limit)])
async def live_feed_json(
    request: Request,
    contest: Contest = Depends(get_contest_by_slug),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return the blackout-aware latest finalized submissions snapshot.

    Counted against the per-IP ``web:live-feed`` window before the contest
    lookup; ``429`` with ``Retry-After`` once it is spent.
    """
    snapshot = await build_contest_live_feed_snapshot(session, contest)
    return JSONResponse(
        headers={"Cache-Control": FEED_CACHE_CONTROL},
        content={
            "live_feed_limit": snapshot.limit,
            "has_more": snapshot.has_more,
            "submissions": [
                {
                    "submission_id": row.submission_id,
                    "created_at": row.created_at.isoformat(),
                    "team": row.team,
                    "problem_label": row.problem_label,
                    "problem_name": row.problem_name,
                    "language_name": row.language_name,
                    "language_icon": row.language_icon,
                    "language_icon_svg_url": str(
                        request.url_for(
                            "static_vendor",
                            path=f"img/devicon/{row.language_icon.split('-')[1]}-original.svg",
                        )
                    ),
                    "verdict": str(row.verdict) if row.verdict is not None else None,
                    "verdict_badge_class": row.verdict_badge_class,
                    "frozen": row.frozen,
                }
                for row in snapshot.rows
            ],
        },
    )


@router.get("/events", name="contest_live_events", dependencies=[Depends(enforce_live_events_slots)])
async def live_feed_events(
    request: Request,
    contest: Contest = Depends(get_contest_by_slug),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Stream lightweight refresh pings as new verdicts finalize for this contest.

    No verdict data leaves the server here; the blackout-aware ``feed.json`` snapshot
    is the sole data source. This only tells the browser when to refetch.

    ``enforce_live_events_slots`` holds a ``web:sse`` per-IP slot for the life of
    the stream (``429`` when exhausted). The request session -- the one
    ``get_contest_by_slug`` resolved the contest through -- is closed here, before
    streaming, so a long-lived connection pins no pooled PostgreSQL connection.
    """
    contest_id = str(contest.id)
    await session.close()

    def _for_this_contest(event: VerdictEvent) -> bool:
        return event.contest_id is None or event.contest_id == contest_id

    async def _stream() -> AsyncIterator[str]:
        runtime = request.app.state.valkey_runtime
        async for chunk in iter_refresh_events(
            open_event_stream=lambda: cast(AsyncGenerator[VerdictEvent], runtime.iter_verdict_events()),
            is_disconnected=request.is_disconnected,
            should_emit=_for_this_contest,
        ):
            yield chunk

    return StreamingResponse(_stream(), media_type="text/event-stream")
