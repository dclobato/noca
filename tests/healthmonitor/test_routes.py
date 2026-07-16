#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the health monitor HTTP routes."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from httpx import ASGITransport, AsyncClient

from healthmonitor.routes.dashboard import router as dashboard_router
from healthmonitor.routes.health import router as health_router
from healthmonitor.routes.status import router as status_router
from healthmonitor.services.presence_probe import ServiceState
from healthmonitor.services.service_registry import MONITORED_SERVICES
from healthmonitor.services.uptime_stats import SLOTS_PER_WINDOW


class _Templates:
    """Capture template rendering arguments."""

    def __init__(self, rendered: dict[str, Any]) -> None:
        self._rendered = rendered

    def TemplateResponse(self, request: object, name: str, context: dict[str, object]) -> HTMLResponse:
        """Return a response while recording the selected template."""
        self._rendered.update({"name": name, "context": context})
        return HTMLResponse("page")


class _EmptyValkeyRuntime:
    """Runtime fake with no presence data and no probe history."""

    is_available = True

    async def hgetall(self, key: str) -> dict[str, str]:
        return {}

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [None] * len(keys)

    async def hmget(self, key: str, fields: list[str]) -> list[str | None]:
        return [None] * len(fields)


def _build_app(valkey_runtime: Any) -> tuple[FastAPI, dict[str, Any]]:
    """Build a minimal health monitor application with a fake runtime."""
    app = FastAPI()
    rendered: dict[str, Any] = {}
    app.state.valkey_runtime = valkey_runtime
    app.state.templates = _Templates(rendered)
    app.include_router(status_router)
    app.include_router(dashboard_router)
    app.include_router(health_router)
    return app, rendered


@pytest.mark.asyncio
async def test_status_page_renders_service_statuses() -> None:
    """The public page renders one status per monitored service."""
    app, rendered = _build_app(_EmptyValkeyRuntime())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert rendered["name"] == "status.html"
    statuses = rendered["context"]["service_statuses"]
    assert len(statuses) == len(MONITORED_SERVICES)
    assert all(status.state is ServiceState.UNAVAILABLE for status in statuses)


@pytest.mark.asyncio
async def test_dashboard_renders_full_heatmaps() -> None:
    """The dashboard renders 60 slots per service without authentication."""
    app, rendered = _build_app(_EmptyValkeyRuntime())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/dashboard")
    assert response.status_code == 200
    assert rendered["name"] == "dashboard.html"
    heatmaps = rendered["context"]["heatmaps"]
    assert len(heatmaps) == len(MONITORED_SERVICES)
    assert all(len(slots) == SLOTS_PER_WINDOW for slots in heatmaps.values())


@pytest.mark.asyncio
async def test_health_endpoint_reports_valkey_state() -> None:
    """The health endpoint mirrors Valkey availability in status and code."""
    app, _ = _build_app(_EmptyValkeyRuntime())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok_response = await client.get("/health")
    assert ok_response.status_code == 200
    assert ok_response.json() == {"status": "ok", "services": {"valkey": True}}

    class _DownRuntime(_EmptyValkeyRuntime):
        is_available = False

    app_down, _ = _build_app(_DownRuntime())
    async with AsyncClient(transport=ASGITransport(app=app_down), base_url="http://test") as client:
        down_response = await client.get("/health")
    assert down_response.status_code == 503
    assert down_response.json()["status"] == "degraded"
