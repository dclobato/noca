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

from healthmonitor import dependencies
from healthmonitor.routes.dashboard import router as dashboard_router
from healthmonitor.routes.health import router as health_router
from healthmonitor.services.presence_probe import ServiceState
from healthmonitor.services.service_registry import MONITORED_SERVICES
from healthmonitor.services.uptime_cache import UptimeHistoryCache
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

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        # A disconnected ValkeyRuntime answers None; the limiter then falls back in-process.
        return None

    async def hmget_many(self, keys: list[str], fields: list[str]) -> list[list[str | None]] | None:
        self.batch_calls = getattr(self, "batch_calls", 0) + 1
        return [await self.hmget(key, fields) for key in keys]


def _build_app(valkey_runtime: Any, *, cache_ttl: int = 300) -> tuple[FastAPI, dict[str, Any]]:
    """Build a minimal health monitor application with a fake runtime."""
    app = FastAPI()
    rendered: dict[str, Any] = {}
    app.state.valkey_runtime = valkey_runtime
    app.state.uptime_cache = UptimeHistoryCache(ttl_seconds=cache_ttl)
    app.state.templates = _Templates(rendered)
    app.include_router(dashboard_router)
    app.include_router(health_router)
    return app, rendered


def _build_rendering_app(valkey_runtime: Any, *, brand_name: str) -> FastAPI:
    """Build an app that renders the real Health Monitor templates."""
    app = FastAPI()
    app.state.valkey_runtime = valkey_runtime
    app.state.uptime_cache = UptimeHistoryCache(ttl_seconds=300)
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
        async def hmget_many(self, keys: list[str], fields: list[str]) -> list[list[str | None]] | None:
            return [["0", "18"] for _ in keys]

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
        async def hmget_many(self, keys: list[str], fields: list[str]) -> list[list[str | None]] | None:
            raise ConnectionError("Valkey unavailable")

    app, _ = _build_app(_FailingHistoryRuntime())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/uptime.json")

    assert response.status_code == 503
    assert response.json()["detail"] == "Uptime history is temporarily unavailable"


@pytest.mark.asyncio
async def test_uptime_data_reports_unavailable_batch_and_caches_nothing() -> None:
    """A ``None`` batch is a 503, and the outage is not pinned into the cache."""

    class _FlakyRuntime(_EmptyValkeyRuntime):
        available = False

        async def hmget_many(self, keys: list[str], fields: list[str]) -> list[list[str | None]] | None:
            if not self.available:
                return None
            return [[None, None] for _ in keys]

    runtime = _FlakyRuntime()
    app, _ = _build_app(runtime)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        failed = await client.get("/uptime.json")
        runtime.available = True
        recovered = await client.get("/uptime.json")

    assert failed.status_code == 503
    assert "cache-control" not in failed.headers
    assert recovered.status_code == 200


@pytest.mark.asyncio
async def test_uptime_data_is_served_from_cache_within_the_probe_interval() -> None:
    """Repeated hits read Valkey once and advertise the remaining window to browsers."""
    runtime = _EmptyValkeyRuntime()
    app, _ = _build_app(runtime, cache_ttl=300)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/uptime.json")
        second = await client.get("/uptime.json")

    assert first.status_code == second.status_code == 200
    assert runtime.batch_calls == 1
    assert first.json() == second.json()
    assert first.headers["cache-control"] == "public, max-age=300"
    assert second.headers["cache-control"].startswith("public, max-age=")


@pytest.mark.asyncio
async def test_prober_invalidation_refreshes_the_cached_payload() -> None:
    """Invalidating the cache (what the prober does) makes the next hit read Valkey again."""
    runtime = _EmptyValkeyRuntime()
    app, _ = _build_app(runtime)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/uptime.json")
        app.state.uptime_cache.invalidate()
        await client.get("/uptime.json")

    assert runtime.batch_calls == 2


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


def _client(app: FastAPI, ip: str = "203.0.113.10") -> AsyncClient:
    """HTTP client presenting a routable client IP to the limiter."""
    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 12345)), base_url="http://test")


def _set_public_limit(monkeypatch: pytest.MonkeyPatch, max_requests: int, *, enabled: bool = True) -> None:
    monkeypatch.setattr(dependencies.settings, "RATE_LIMIT_ENABLED", enabled)
    monkeypatch.setattr(dependencies.settings, "RATE_LIMIT_MAX_REQUESTS", max_requests)
    monkeypatch.setattr(dependencies.settings, "RATE_LIMIT_WINDOW_SECONDS", 60)
    monkeypatch.setattr(dependencies.settings, "RATE_LIMIT_TRUSTED_CIDRS", "127.0.0.0/8")


@pytest.mark.asyncio
async def test_public_routes_share_one_per_ip_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """``/``, ``/refresh`` and ``/uptime.json`` draw from the same per-IP budget."""
    _set_public_limit(monkeypatch, 3)
    app, _ = _build_app(_EmptyValkeyRuntime())
    async with _client(app) as client:
        assert (await client.get("/")).status_code == 200
        assert (await client.get("/refresh")).status_code == 200
        assert (await client.get("/uptime.json")).status_code == 200
        rejected = await client.get("/uptime.json")
    async with _client(app, ip="198.51.100.7") as other:
        unaffected = await other.get("/")

    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"] == "60"
    assert rejected.json() == {"detail": "Dashboard rate limit exceeded."}
    assert unaffected.status_code == 200


@pytest.mark.asyncio
async def test_public_rate_limit_trusted_network_and_disable_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loopback bypasses the window, and the switch turns it off for everyone."""
    _set_public_limit(monkeypatch, 1)
    app, _ = _build_app(_EmptyValkeyRuntime())
    async with _client(app, ip="127.0.0.1") as trusted:
        statuses = [(await trusted.get("/health")).status_code for _ in range(3)]
        statuses += [(await trusted.get("/uptime.json")).status_code for _ in range(3)]
    assert statuses == [200] * 6

    _set_public_limit(monkeypatch, 1, enabled=False)
    async with _client(app) as client:
        assert [(await client.get("/")).status_code for _ in range(3)] == [200] * 3


@pytest.mark.asyncio
async def test_health_route_uses_its_own_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    """``/health`` is limited by the shared health settings, independently of the dashboard."""
    _set_public_limit(monkeypatch, 1)
    monkeypatch.setattr(dependencies.settings, "HEALTH_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(dependencies.settings, "HEALTH_RATE_LIMIT_MAX_REQUESTS", 2)
    monkeypatch.setattr(dependencies.settings, "HEALTH_RATE_LIMIT_WINDOW_SECONDS", 60)
    monkeypatch.setattr(dependencies.settings, "HEALTH_RATE_LIMIT_TRUSTED_CIDRS", "10.0.0.0/8")
    app, _ = _build_app(_EmptyValkeyRuntime())
    async with _client(app) as client:
        assert (await client.get("/uptime.json")).status_code == 200
        first, second = await client.get("/health"), await client.get("/health")
        rejected = await client.get("/health")

    assert first.status_code == second.status_code == 200
    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"] == "60"
    assert rejected.json() == {"detail": "Health endpoint rate limit exceeded."}
