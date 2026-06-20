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

from web.services.contest_service import get_active_contests_grouped

logger = logging.getLogger(__name__)

router = APIRouter(tags=["assets"])


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

    templates = request.app.state.templates
    return _html(
        templates.TemplateResponse(
            request,
            "contests.html",
            {
                "running_contests": contests.live_contests,
                "upcoming_contests": contests.upcoming_contests,
                "past_contests": contests.past_contests,
            },
        )
    )
