#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public environment status page."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from healthmonitor.services.presence_probe import read_service_statuses, unknown_service_statuses

router = APIRouter(tags=["status"])
logger = logging.getLogger(__name__)


@router.get("/", response_class=HTMLResponse, name="healthmon_status")
async def environment_status(request: Request) -> Response:
    """Render the public up/down status of every monitored service."""
    try:
        service_statuses = await read_service_statuses(request.app.state.valkey_runtime)
    except Exception:
        logger.exception("Status read failed; rendering unknown states")
        service_statuses = unknown_service_statuses()
    return request.app.state.templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "status.html",
        {"service_statuses": service_statuses},
    )
