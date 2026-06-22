#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the authenticated Arena presence endpoints."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import arena.dependencies.auth as auth_module
import arena.routes.presence as presence_module
from arena.dependencies.auth import _refresh_presence, get_current_arena_user
from arena.routes.presence import ARENA_PRESENCE_DOMAIN
from arena.routes.presence import router as arena_presence_router
from shared.services.user_presence import user_live_key


class _RecordingRuntime:
    """In-memory Valkey stand-in recording eval/mget calls for assertions."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.eval_calls: list[tuple[str, int, tuple[str, ...]]] = []
        self.mget_calls: list[list[str]] = []

    async def eval(self, script: str, numkeys: int, *args: str) -> int:
        self.eval_calls.append((script, numkeys, args))
        if "ZADD" in script:  # mark online sets the live key
            self.store[args[0]] = "1"
        elif "ZREM" in script:  # mark offline clears the live key
            self.store.pop(args[0], None)
        return 1

    async def mget(self, keys: list[str]) -> list[str | None]:
        self.mget_calls.append(keys)
        return [self.store.get(key) for key in keys]


def _build_app(current_user: object | None, runtime: _RecordingRuntime) -> FastAPI:
    """Build a minimal app wiring the presence router, an override user, and runtime."""
    app = FastAPI()
    app.include_router(arena_presence_router)
    app.state.valkey_runtime = runtime

    async def _override_current_user():
        return current_user

    app.dependency_overrides[get_current_arena_user] = _override_current_user
    return app


def _user() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4())


@pytest.mark.asyncio
async def test_heartbeat_requires_authentication() -> None:
    """A guest heartbeat is rejected and never touches Valkey."""
    runtime = _RecordingRuntime()
    app = _build_app(None, runtime)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/arena/presence/heartbeat")

    assert response.status_code == 401
    assert runtime.eval_calls == []


@pytest.mark.asyncio
async def test_status_requires_authentication_and_skips_valkey() -> None:
    """A guest status request is rejected before the body is read or Valkey is touched."""
    runtime = _RecordingRuntime()
    app = _build_app(None, runtime)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/arena/presence/status", json={"ids": ["u1"]})

    assert response.status_code == 401
    assert runtime.mget_calls == []
    assert runtime.eval_calls == []


@pytest.mark.asyncio
async def test_heartbeat_marks_current_user_online() -> None:
    """An authenticated heartbeat marks the current user online once."""
    runtime = _RecordingRuntime()
    user = _user()
    app = _build_app(user, runtime)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/arena/presence/heartbeat")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "enabled": True}
    assert len(runtime.eval_calls) == 1
    assert runtime.store.get(user_live_key(ARENA_PRESENCE_DOMAIN, str(user.id))) == "1"


@pytest.mark.asyncio
async def test_status_returns_only_online_ids_for_batch() -> None:
    """Status returns only the online ids; offline ids are simply absent."""
    runtime = _RecordingRuntime()
    online_user = _user()
    runtime.store[user_live_key(ARENA_PRESENCE_DOMAIN, str(online_user.id))] = "1"

    app = _build_app(_user(), runtime)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/arena/presence/status",
            json={"ids": [str(online_user.id), "missing"]},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["online"] == [str(online_user.id)]


@pytest.mark.asyncio
async def test_status_rejects_malformed_body() -> None:
    """A non-JSON body and a non-list 'ids' both yield 400 without a Valkey read."""
    runtime = _RecordingRuntime()
    app = _build_app(_user(), runtime)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        bad_json = await client.post(
            "/arena/presence/status",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        bad_shape = await client.post("/arena/presence/status", json={"ids": "u1"})

    assert bad_json.status_code == 400
    assert bad_shape.status_code == 400
    assert runtime.mget_calls == []


def _request(method: str, runtime: _RecordingRuntime) -> SimpleNamespace:
    return SimpleNamespace(method=method, app=SimpleNamespace(state=SimpleNamespace(valkey_runtime=runtime)))


@pytest.mark.asyncio
async def test_navigation_marking_only_runs_on_get(monkeypatch: pytest.MonkeyPatch) -> None:
    """The auth dependency marks online on GET but not on the POST presence endpoints."""
    monkeypatch.setattr(auth_module.settings, "PRESENCE_ENABLED", True)
    user = _user()

    get_runtime = _RecordingRuntime()
    await _refresh_presence(_request("GET", get_runtime), user)
    assert len(get_runtime.eval_calls) == 1

    post_runtime = _RecordingRuntime()
    await _refresh_presence(_request("POST", post_runtime), user)
    assert post_runtime.eval_calls == []


@pytest.mark.asyncio
async def test_endpoints_are_inert_when_presence_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """With presence disabled both endpoints return inert payloads and skip Valkey."""
    monkeypatch.setattr(presence_module.settings, "PRESENCE_ENABLED", False)
    runtime = _RecordingRuntime()
    app = _build_app(_user(), runtime)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        heartbeat = await client.post("/arena/presence/heartbeat")
        status = await client.post("/arena/presence/status", json={"ids": ["u1"]})

    assert heartbeat.json() == {"ok": False, "enabled": False}
    assert status.json() == {"enabled": False, "online": []}
    assert runtime.eval_calls == []
    assert runtime.mget_calls == []
