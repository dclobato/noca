#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from arena.routes.health import health


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
    def __init__(self, app: object) -> None:
        self.app = app


class _UnavailableSessionFactory:
    def __call__(self) -> _UnavailableSessionFactory:
        return self

    async def __aenter__(self) -> _UnavailableSessionFactory:
        raise ConnectionRefusedError(111, "database unavailable")

    async def __aexit__(self, *args: object) -> None:
        return None


def _state(
    *,
    engine: object,
    session_factory: object,
    valkey_available: bool,
) -> SimpleNamespace:
    return SimpleNamespace(
        arena_db_engine=engine,
        arena_db_session=session_factory,
        valkey_runtime=_FakeValkeyRuntime(
            is_available=valkey_available,
            pending_count=1,
        ),
        jwt_service=object(),
        image_service=object(),
        email_service=object(),
        arena_templates=object(),
        rating_poller_task=_FakeTask(done=False),
    )


@pytest.mark.asyncio
async def test_arena_health_reports_healthy_services(engine) -> None:
    request = _FakeRequest(
        app=SimpleNamespace(
            state=_state(
                engine=engine,
                session_factory=async_sessionmaker(engine, expire_on_commit=False),
                valkey_available=True,
            )
        )
    )

    response = await health(request)  # type: ignore[arg-type]
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["services"]["database_engine"]["available"] is True
    assert payload["services"]["valkey_runtime"]["available"] is True
    assert payload["services"]["rating_poller"]["running"] is True


@pytest.mark.asyncio
async def test_arena_health_returns_degraded_response_for_database_failure() -> None:
    request = _FakeRequest(
        app=SimpleNamespace(
            state=_state(
                engine=object(),
                session_factory=_UnavailableSessionFactory(),
                valkey_available=True,
            )
        )
    )

    response = await health(request)  # type: ignore[arg-type]
    payload = json.loads(response.body)

    assert response.status_code == 503
    assert payload["status"] == "degraded"
    assert payload["services"]["database_engine"]["available"] is False
    assert "database unavailable" not in response.body.decode()


@pytest.mark.asyncio
async def test_arena_health_returns_degraded_response_for_valkey_failure(
    engine,
) -> None:
    request = _FakeRequest(
        app=SimpleNamespace(
            state=_state(
                engine=engine,
                session_factory=async_sessionmaker(engine, expire_on_commit=False),
                valkey_available=False,
            )
        )
    )

    response = await health(request)  # type: ignore[arg-type]
    payload = json.loads(response.body)

    assert response.status_code == 503
    assert payload["status"] == "degraded"
    assert payload["services"]["valkey_runtime"]["available"] is False
