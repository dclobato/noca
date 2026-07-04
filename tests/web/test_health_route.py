#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from web.models.contest import Contest
from web.routes.health import enforce_web_health_rate_limit, health


class _FakeValkeyRuntime:
    def __init__(self, *, is_available: bool, pending_count: int) -> None:
        self.is_available = is_available
        self.pending_count = pending_count


class _FakeTask:
    def __init__(self, *, done: bool) -> None:
        self._done = done

    def done(self) -> bool:
        return self._done


class _FakeRequest:
    def __init__(self, app: object, *, client_ip: str = "203.0.113.30") -> None:
        self.app = app
        self.headers: dict[str, str] = {}
        self.client = SimpleNamespace(host=client_ip)


class _UnavailableSessionFactory:
    def __call__(self) -> _UnavailableSessionFactory:
        return self

    async def __aenter__(self) -> _UnavailableSessionFactory:
        raise ConnectionRefusedError(111, "database unavailable")

    async def __aexit__(self, *args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_health_reports_contest_counts_and_lifespan_services(
    engine,
    session: AsyncSession,
    uberadmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    session.add_all(
        [
            Contest(
                contest_name="Live Contest",
                contest_url="https://live.example.com",
                login_slug="live-contest",
                start_time=now - timedelta(minutes=30),
                duration_minutes=120,
                stop_answers_after=120,
                stop_updating_scoreboard=120,
                clarifications_timeout_minutes=10,
                created_by_uberadmin_id=uberadmin.id,
                active=True,
            ),
            Contest(
                contest_name="Past Contest",
                contest_url="https://past.example.com",
                login_slug="past-contest",
                start_time=now - timedelta(hours=4),
                duration_minutes=60,
                stop_answers_after=60,
                stop_updating_scoreboard=60,
                clarifications_timeout_minutes=10,
                created_by_uberadmin_id=uberadmin.id,
                active=True,
            ),
            Contest(
                contest_name="Upcoming Contest",
                contest_url="https://upcoming.example.com",
                login_slug="upcoming-contest",
                start_time=now + timedelta(hours=2),
                duration_minutes=90,
                stop_answers_after=90,
                stop_updating_scoreboard=90,
                clarifications_timeout_minutes=10,
                created_by_uberadmin_id=uberadmin.id,
                active=True,
            ),
            Contest(
                contest_name="Inactive Contest",
                contest_url="https://inactive.example.com",
                login_slug="inactive-contest",
                start_time=now + timedelta(days=1),
                duration_minutes=90,
                stop_answers_after=90,
                stop_updating_scoreboard=90,
                clarifications_timeout_minutes=10,
                created_by_uberadmin_id=uberadmin.id,
                active=False,
            ),
        ]
    )
    await session.commit()

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    state = SimpleNamespace(
        db_engine=engine,
        db_session=session_factory,
        valkey_runtime=_FakeValkeyRuntime(is_available=True, pending_count=2),
        auth_service=object(),
        image_service=object(),
        email_service=object(),
        templates=object(),
        clarification_reaper_task=_FakeTask(done=False),
    )
    request = _FakeRequest(app=SimpleNamespace(state=state))

    monkeypatch.setattr("web.routes.health.settings.ENABLE_CLARIFICATION_REAPER", True)

    response = await health(request)  # type: ignore[arg-type]
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["contests"] == {
        "inactive_contests": 1,
        "past_contests": 1,
        "live_contests": 1,
        "upcoming_contests": 1,
    }
    assert payload["services"] == {
        "database_engine": {"registered": True, "available": True},
        "database_session_factory": {"registered": True},
        "valkey_runtime": {"registered": True, "available": True, "pending_commands": 2},
        "auth_service": {"registered": True},
        "image_service": {"registered": True},
        "email_service": {"registered": True},
        "templates": {"registered": True},
        "clarification_reaper": {"enabled": True, "running": True},
    }


@pytest.mark.asyncio
async def test_health_returns_degraded_response_when_database_is_unavailable() -> None:
    state = SimpleNamespace(
        db_engine=object(),
        db_session=_UnavailableSessionFactory(),
        valkey_runtime=_FakeValkeyRuntime(is_available=True, pending_count=0),
    )
    request = _FakeRequest(app=SimpleNamespace(state=state))

    response = await health(request)  # type: ignore[arg-type]
    payload = json.loads(response.body)

    assert response.status_code == 503
    assert payload["status"] == "degraded"
    assert payload["services"]["database_engine"]["available"] is False
    assert "database unavailable" not in response.body.decode()


@pytest.mark.asyncio
async def test_health_returns_degraded_response_when_valkey_is_unavailable(
    engine,
) -> None:
    state = SimpleNamespace(
        db_engine=engine,
        db_session=async_sessionmaker(engine, expire_on_commit=False),
        valkey_runtime=_FakeValkeyRuntime(is_available=False, pending_count=3),
    )
    request = _FakeRequest(app=SimpleNamespace(state=state))

    response = await health(request)  # type: ignore[arg-type]
    payload = json.loads(response.body)

    assert response.status_code == 503
    assert payload["status"] == "degraded"
    assert payload["services"]["valkey_runtime"]["available"] is False


@pytest.mark.asyncio
async def test_web_health_rate_limit_dependency_rejects_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("web.routes.health.settings.HEALTH_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr("web.routes.health.settings.HEALTH_RATE_LIMIT_WINDOW_SECONDS", 60)
    monkeypatch.setattr("web.routes.health.settings.HEALTH_RATE_LIMIT_MAX_REQUESTS", 1)
    monkeypatch.setattr("web.routes.health.settings.HEALTH_RATE_LIMIT_TRUSTED_CIDRS", "127.0.0.0/8")
    monkeypatch.setattr("web.routes.health._fallback_health_limiter._buckets", {})
    request = _FakeRequest(app=SimpleNamespace(state=SimpleNamespace(valkey_runtime=None)))

    await enforce_web_health_rate_limit(request)  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as exc_info:
        await enforce_web_health_rate_limit(request)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_web_health_rate_limit_dependency_trusts_local_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("web.routes.health.settings.HEALTH_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr("web.routes.health.settings.HEALTH_RATE_LIMIT_WINDOW_SECONDS", 60)
    monkeypatch.setattr("web.routes.health.settings.HEALTH_RATE_LIMIT_MAX_REQUESTS", 1)
    monkeypatch.setattr("web.routes.health.settings.HEALTH_RATE_LIMIT_TRUSTED_CIDRS", "127.0.0.0/8")
    monkeypatch.setattr("web.routes.health._fallback_health_limiter._buckets", {})
    request = _FakeRequest(app=SimpleNamespace(state=SimpleNamespace(valkey_runtime=None)), client_ip="127.0.0.1")

    await enforce_web_health_rate_limit(request)  # type: ignore[arg-type]
    await enforce_web_health_rate_limit(request)  # type: ignore[arg-type]
