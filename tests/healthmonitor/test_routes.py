#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the health monitor HTTP routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient

from healthmonitor.routes.dashboard import router as dashboard_router
from healthmonitor.routes.health import router as health_router
from healthmonitor.services.presence_probe import ServiceState
from healthmonitor.services.service_registry import MONITORED_SERVICES
from healthmonitor.services.uptime_stats import SLOTS_PER_WINDOW

_HEALTHMON_DIR = Path(__file__).resolve().parents[2] / "healthmonitor"


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
    app.include_router(dashboard_router)
    app.include_router(health_router)
    return app, rendered


def _build_rendering_app(valkey_runtime: Any, *, brand_name: str) -> FastAPI:
    """Build an app that renders the real Health Monitor templates."""
    app = FastAPI()
    app.state.valkey_runtime = valkey_runtime
    templates = Jinja2Templates(directory=_HEALTHMON_DIR / "template")
    templates.env.globals.update(app_version="test", brand_name=brand_name)
    app.state.templates = templates

    async def asset_stub(path: str) -> Response:
        """Provide named static routes for template URL generation."""
        return Response(path)

    for route_name in (
        "static_vendor",
        "healthmon_static_css",
        "static_shared_js",
        "healthmon_static_js",
    ):
        app.add_api_route(
            f"/assets/{route_name}/{{path:path}}",
            asset_stub,
            name=route_name,
            include_in_schema=False,
        )
    app.include_router(dashboard_router)
    return app


@pytest.mark.asyncio
async def test_dashboard_renders_service_statuses() -> None:
    """The dashboard renders every service status without authentication."""
    app, rendered = _build_app(_EmptyValkeyRuntime())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert rendered["name"] == "dashboard.html"
    statuses = rendered["context"]["service_statuses"]
    assert len(statuses) == len(MONITORED_SERVICES)
    assert all(status.state is ServiceState.UNAVAILABLE for status in statuses)


@pytest.mark.asyncio
async def test_dashboard_refresh_renders_live_fragment() -> None:
    """The HTMX refresh route returns the shared live dashboard fragment."""
    app, rendered = _build_app(_EmptyValkeyRuntime())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/refresh", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert rendered["name"] == "_dashboard_content.html"
    assert len(rendered["context"]["service_statuses"]) == len(MONITORED_SERVICES)


@pytest.mark.asyncio
async def test_dashboard_renders_exact_brand_and_accessible_live_controls() -> None:
    """The real dashboard honors its brand and exposes operable uptime cells."""
    app = _build_rendering_app(_EmptyValkeyRuntime(), brand_name="Contest Operations")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "<span>Contest Operations</span>" in response.text
    assert "Contest Operations Health Monitor" not in response.text
    assert 'name="htmx-config"' in response.text
    assert "content='{\"allowEval\": false}'" in response.text
    assert 'hx-trigger="every 30s"' in response.text
    assert "echarts.min.js" in response.text
    assert "noca-echarts-theme.js" in response.text
    assert "data-healthmon-heatmap" in response.text
    assert "data-healthmon-uptime-table" in response.text
    assert "/uptime.json" in response.text
    assert 'http-equiv="refresh"' not in response.text


@pytest.mark.asyncio
async def test_uptime_data_returns_all_service_slots() -> None:
    """The JSON chart endpoint returns the complete ordered uptime window."""
    app, _ = _build_app(_EmptyValkeyRuntime())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/uptime.json")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["services"]) == len(MONITORED_SERVICES)
    assert [service["key"] for service in payload["services"]] == [
        service.worker_class.value for service in MONITORED_SERVICES
    ]
    assert all(len(service["slots"]) == SLOTS_PER_WINDOW for service in payload["services"])
    assert all(slot["uptime_pct"] is None for slot in payload["services"][0]["slots"])


@pytest.mark.asyncio
async def test_uptime_data_preserves_zero_percent_slots() -> None:
    """A fully unavailable slot remains numeric zero in the chart contract."""

    class _UnavailableHistoryRuntime(_EmptyValkeyRuntime):
        async def hmget(self, key: str, fields: list[str]) -> list[str | None]:
            return ["0", "18"]

    app, _ = _build_app(_UnavailableHistoryRuntime())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/uptime.json")

    assert response.status_code == 200
    newest_slot = response.json()["services"][0]["slots"][-1]
    assert newest_slot["uptime_pct"] == 0.0
    assert newest_slot["up"] == 0
    assert newest_slot["total"] == 18


@pytest.mark.asyncio
async def test_uptime_data_reports_backend_failure() -> None:
    """The JSON chart endpoint returns 503 when uptime history cannot be read."""

    class _FailingHistoryRuntime(_EmptyValkeyRuntime):
        async def hmget(self, key: str, fields: list[str]) -> list[str | None]:
            raise ConnectionError("Valkey unavailable")

    app, _ = _build_app(_FailingHistoryRuntime())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/uptime.json")

    assert response.status_code == 503
    assert response.json()["detail"] == "Uptime history is temporarily unavailable"


@pytest.mark.asyncio
async def test_old_dashboard_url_is_gone() -> None:
    """The dashboard moved to the root; the old URL no longer exists."""
    app, _ = _build_app(_EmptyValkeyRuntime())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/dashboard")
    assert response.status_code == 404


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
