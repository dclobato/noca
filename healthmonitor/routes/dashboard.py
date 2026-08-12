#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public uptime dashboard with per-service 30-day heatmaps."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from healthmonitor.services.presence_probe import (
    ServiceState,
    read_service_statuses,
    unknown_service_statuses,
)
from healthmonitor.services.service_registry import MONITORED_SERVICES
from healthmonitor.services.uptime_stats import read_service_heatmaps

router = APIRouter(tags=["dashboard"])
logger = logging.getLogger(__name__)


class UptimeSlotPayload(BaseModel):
    """Public uptime values for one 12-hour slot."""

    started_at: datetime
    uptime_pct: float | None
    up: int
    total: int


class ServiceUptimePayload(BaseModel):
    """Display metadata and uptime history for one monitored service."""

    key: str
    title: str
    slots: list[UptimeSlotPayload]


class UptimeDashboardPayload(BaseModel):
    """JSON contract consumed by the dashboard ECharts heatmaps."""

    checked_at: datetime
    services: list[ServiceUptimePayload]


async def _dashboard_context(request: Request) -> dict[str, object]:
    """Build the live status context shared by dashboard HTML responses."""
    valkey_runtime = request.app.state.valkey_runtime
    try:
        service_statuses = await read_service_statuses(valkey_runtime)
    except Exception:
        logger.exception("Dashboard read failed; rendering unknown states")
        service_statuses = unknown_service_statuses()
    summary = {state: sum(1 for status in service_statuses if status.state is state) for state in ServiceState}
    return {
        "service_statuses": service_statuses,
        "summary": summary,
        "checked_at": datetime.now(UTC),
    }


@router.get("/", response_class=HTMLResponse, name="healthmon_dashboard")
async def uptime_dashboard(request: Request) -> Response:
    """Render live statuses plus the 60-slot uptime heatmap of every service."""
    return request.app.state.templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "dashboard.html",
        await _dashboard_context(request),
    )


@router.get("/refresh", response_class=HTMLResponse, name="healthmon_dashboard_refresh")
async def uptime_dashboard_refresh(request: Request) -> Response:
    """Render the HTMX fragment that refreshes dashboard status and history."""
    return request.app.state.templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "_dashboard_content.html",
        await _dashboard_context(request),
    )


@router.get("/uptime.json", name="healthmon_uptime_data")
async def uptime_dashboard_data(request: Request) -> UptimeDashboardPayload:
    """Return the per-service uptime history consumed by the ECharts heatmaps."""
    try:
        heatmaps = await read_service_heatmaps(request.app.state.valkey_runtime, MONITORED_SERVICES)
    except Exception as error:
        logger.exception("Uptime history read failed")
        raise HTTPException(status_code=503, detail="Uptime history is temporarily unavailable") from error

    return UptimeDashboardPayload(
        checked_at=datetime.now(UTC),
        services=[
            ServiceUptimePayload(
                key=service.worker_class.value,
                title=service.title,
                slots=[
                    UptimeSlotPayload(
                        started_at=slot.slot_start,
                        uptime_pct=(round(slot.uptime_pct, 1) if slot.uptime_pct is not None else None),
                        up=slot.up,
                        total=slot.total,
                    )
                    for slot in heatmaps[service.worker_class]
                ],
            )
            for service in MONITORED_SERVICES
        ],
    )
