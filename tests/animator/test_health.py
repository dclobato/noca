#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the animator health endpoint."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError

from animator.config import settings as animator_settings
from animator.routes.health import router as health_router

_SECRET_MARKER = "super-secret-dsn-detail"


class _FakeSession:
    """Async session whose execute either succeeds or raises a marked error."""

    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, statement: object) -> None:
        if self._fail:
            raise OperationalError(f"SELECT 1 [{_SECRET_MARKER}]", None, Exception(_SECRET_MARKER))


class _FakeSessionFactory:
    """Callable that yields a fake session on each call."""

    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    def __call__(self) -> _FakeSession:
        return _FakeSession(fail=self._fail)


class _FakeValkey:
    """Minimal Valkey runtime stand-in.

    ``eval`` returns ``None`` so the health limiter falls back to its in-memory
    limiter, exercising the rate-limit path without a real Valkey.
    """

    def __init__(self, *, available: bool) -> None:
        self.is_available = available
        self.pending_count = 0

    async def eval(self, script: str, numkeys: int, *args: str) -> None:
        return None


def _build_app(
    *,
    session_factory: Any | None,
    valkey: Any | None,
    engine: Any | None = object(),
) -> FastAPI:
    app = FastAPI()
    if session_factory is not None:
        app.state.db_session = session_factory
    if engine is not None:
        app.state.db_engine = engine
    if valkey is not None:
        app.state.valkey_runtime = valkey
    app.include_router(health_router)
    return app


async def _get_health(app: FastAPI) -> tuple[int, dict[str, Any]]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    return response.status_code, response.json()


@pytest.mark.asyncio
async def test_healthy() -> None:
    """Both PostgreSQL and Valkey reachable yields ok/200."""
    app = _build_app(session_factory=_FakeSessionFactory(fail=False), valkey=_FakeValkey(available=True))
    status_code, body = await _get_health(app)

    assert status_code == 200
    assert body["status"] == "ok"
    assert body["services"]["database_engine"]["available"] is True
    assert body["services"]["valkey_runtime"]["available"] is True


@pytest.mark.asyncio
async def test_postgres_down() -> None:
    """A failing SELECT 1 degrades status without leaking exception text."""
    app = _build_app(session_factory=_FakeSessionFactory(fail=True), valkey=_FakeValkey(available=True))
    status_code, body = await _get_health(app)

    assert status_code == 503
    assert body["status"] == "degraded"
    assert body["services"]["database_engine"]["available"] is False
    assert _SECRET_MARKER not in str(body)


@pytest.mark.asyncio
async def test_valkey_down() -> None:
    """An unavailable Valkey runtime degrades status."""
    app = _build_app(session_factory=_FakeSessionFactory(fail=False), valkey=_FakeValkey(available=False))
    status_code, body = await _get_health(app)

    assert status_code == 503
    assert body["status"] == "degraded"
    assert body["services"]["database_engine"]["available"] is True
    assert body["services"]["valkey_runtime"]["available"] is False


@pytest.mark.asyncio
async def test_absent_state() -> None:
    """With no registered backends the endpoint reports degraded, not error."""
    app = _build_app(session_factory=None, valkey=None, engine=None)
    status_code, body = await _get_health(app)

    assert status_code == 503
    assert body["status"] == "degraded"
    assert body["services"]["database_engine"]["registered"] is False
    assert body["services"]["database_session_factory"]["registered"] is False
    assert body["services"]["valkey_runtime"]["registered"] is False


async def _get_codes(app: FastAPI, *, client: tuple[str, int], count: int) -> list[int]:
    """Issue *count* /health requests from a fixed source IP and return status codes."""
    transport = ASGITransport(app=app, client=client)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        return [(await http_client.get("/health")).status_code for _ in range(count)]


@pytest.mark.asyncio
async def test_rate_limit_rejects_untrusted_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """An untrusted client exceeding the window limit receives 429 before the DB probe."""
    monkeypatch.setattr(animator_settings, "HEALTH_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(animator_settings, "HEALTH_RATE_LIMIT_MAX_REQUESTS", 2)
    monkeypatch.setattr(animator_settings, "HEALTH_RATE_LIMIT_WINDOW_SECONDS", 300)

    app = _build_app(session_factory=_FakeSessionFactory(fail=False), valkey=_FakeValkey(available=True))
    codes = await _get_codes(app, client=("203.0.113.7", 5000), count=3)

    assert codes[:2] == [200, 200]
    assert codes[2] == 429


@pytest.mark.asyncio
async def test_rate_limit_bypasses_trusted_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """A trusted-CIDR probe is never rate limited even past the configured limit."""
    monkeypatch.setattr(animator_settings, "HEALTH_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(animator_settings, "HEALTH_RATE_LIMIT_MAX_REQUESTS", 2)
    monkeypatch.setattr(animator_settings, "HEALTH_RATE_LIMIT_WINDOW_SECONDS", 300)
    monkeypatch.setattr(animator_settings, "HEALTH_RATE_LIMIT_TRUSTED_CIDRS", "127.0.0.0/8,::1/128")

    app = _build_app(session_factory=_FakeSessionFactory(fail=False), valkey=_FakeValkey(available=True))
    codes = await _get_codes(app, client=("127.0.0.1", 5000), count=4)

    assert codes == [200, 200, 200, 200]
