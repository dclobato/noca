#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import logging
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse

from web.services.contest_service import get_active_contests_grouped, sort_past_contests_recent_first

logger = logging.getLogger(__name__)

router = APIRouter(tags=["assets"])

# Preview count of past contests shown on the gateway page before linking out
# to the full /contests/past history.
PAST_CONTESTS_PREVIEW_LIMIT = 6


def _html(response: Any) -> HTMLResponse:
    return cast(HTMLResponse, response)


@router.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(
        Path(__file__).parent.parent / "assets" / "favicon.ico",
        media_type="image/x-icon",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/", response_class=HTMLResponse)
@router.get("/contests", response_class=HTMLResponse)
async def contests_list(request: Request) -> HTMLResponse:
    async with request.app.state.db_session() as session:
        contests = await get_active_contests_grouped(session)

    past_contests = sort_past_contests_recent_first(contests.past_contests)
    templates = request.app.state.templates
    return _html(
        templates.TemplateResponse(
            request,
            "contests.html",
            {
                "running_contests": contests.live_contests,
                "upcoming_contests": contests.upcoming_contests,
                "past_contests": past_contests[:PAST_CONTESTS_PREVIEW_LIMIT],
                "past_contests_total": len(past_contests),
                "past_contests_preview_limit": PAST_CONTESTS_PREVIEW_LIMIT,
                "open_contests_total": len(contests.live_contests) + len(contests.upcoming_contests),
            },
        )
    )


@router.get("/contests/past", response_class=HTMLResponse, name="contests_past")
async def contests_past(request: Request) -> HTMLResponse:
    async with request.app.state.db_session() as session:
        contests = await get_active_contests_grouped(session)

    templates = request.app.state.templates
    return _html(
        templates.TemplateResponse(
            request,
            "contests_past.html",
            {"past_contests": sort_past_contests_recent_first(contests.past_contests)},
        )
    )
