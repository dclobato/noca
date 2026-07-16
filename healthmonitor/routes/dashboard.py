#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public uptime dashboard with per-service 30-day heatmaps."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from healthmonitor.services.presence_probe import read_service_statuses, unknown_service_statuses
from healthmonitor.services.service_registry import MONITORED_SERVICES
from healthmonitor.services.uptime_stats import SlotStat, read_service_heatmaps
from shared.services.valkey_service import WorkerClass

router = APIRouter(tags=["dashboard"])
logger = logging.getLogger(__name__)


@router.get("/dashboard", response_class=HTMLResponse, name="healthmon_dashboard")
async def uptime_dashboard(request: Request) -> Response:
    """Render live statuses plus the 60-slot uptime heatmap of every service."""
    valkey_runtime = request.app.state.valkey_runtime
    heatmaps: dict[WorkerClass, list[SlotStat]] = {}
    try:
        service_statuses = await read_service_statuses(valkey_runtime)
        heatmaps = await read_service_heatmaps(valkey_runtime, MONITORED_SERVICES)
    except Exception:
        logger.exception("Dashboard read failed; rendering unknown states")
        service_statuses = unknown_service_statuses()
    return request.app.state.templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "dashboard.html",
        {"service_statuses": service_statuses, "heatmaps": heatmaps},
    )
