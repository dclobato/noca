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
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.routing import NoMatchFound

import arena.dependencies.auth as auth_module
import arena.routes.presence as presence_module
from arena.config import settings as arena_settings
from arena.dependencies.auth import _refresh_presence, get_current_arena_user
from arena.main import app as arena_app
from arena.routes.presence import ARENA_PRESENCE_DOMAIN
from arena.routes.presence import router as arena_presence_router
from arena.template_globals import heartbeat_config, session_heartbeat_seconds
from shared.enumerations import ArenaRole
from shared.services.user_presence import user_live_key
from tests.arena._admin_problem_app import build_admin_app, create_user, login_token


class _RecordingRuntime:
    """In-memory Valkey stand-in recording presence eval/mget calls for assertions.

    The presence router also carries the per-user read ceiling, which runs its
    own counter script through the same ``eval``. Only the presence scripts are
    recorded, so "this guest never touched presence state" stays exactly what
    the assertions below say -- and stays true regardless of what an unrelated
    limiter counts before the auth check.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.eval_calls: list[tuple[str, int, tuple[str, ...]]] = []
        self.mget_calls: list[list[str]] = []

    async def eval(self, script: str, numkeys: int, *args: str) -> int:
        if "ZADD" in script:  # mark online sets the live key
            self.eval_calls.append((script, numkeys, args))
            self.store[args[0]] = "1"
        elif "ZREM" in script:  # mark offline clears the live key
            self.eval_calls.append((script, numkeys, args))
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


def _request_for(app: FastAPI) -> Request:
    """Build a minimal request bound to `app`, enough for `url_for` resolution."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("testserver", 80),
            "path": "/",
            "query_string": b"",
            "headers": [],
            "app": app,
            "router": app.router,
        }
    )


class TestHeartbeatKeepaliveDecoupling:
    """The heartbeat is the sliding session's keepalive, so it must not follow the presence flag.

    A page open for a long edit makes no other request. Gating the heartbeat on
    ``NOCA_ARENA_PRESENCE_ENABLED`` made session lifetime depend on whether the
    green-dot feature happened to be on, which ended long edits at the login
    page with the form discarded.
    """

    def test_config_is_emitted_even_when_presence_is_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With presence off the client is still configured, with dots switched off."""
        monkeypatch.setattr(arena_settings, "PRESENCE_ENABLED", False)
        app = FastAPI()
        app.include_router(arena_presence_router)

        config = heartbeat_config(_request_for(app))

        assert config is not None
        assert config["presence_enabled"] is False
        assert config["heartbeat_url"].endswith("/arena/presence/heartbeat")

    def test_keepalive_interval_lands_inside_the_refresh_window(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With presence off the interval must still refresh the token before half-life."""
        monkeypatch.setattr(arena_settings, "PRESENCE_ENABLED", False)
        monkeypatch.setattr(arena_settings, "JWT_EXPIRE_SECONDS", 3600)

        interval = session_heartbeat_seconds()

        assert interval < arena_settings.JWT_EXPIRE_SECONDS // 2
        assert interval >= 60

    def test_presence_cadence_wins_when_presence_is_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With presence on the dots set the cadence, so no extra requests are added."""
        monkeypatch.setattr(arena_settings, "PRESENCE_ENABLED", True)
        monkeypatch.setattr(arena_settings, "PRESENCE_HEARTBEAT_SECONDS", 30)

        assert session_heartbeat_seconds() == 30

    def test_the_real_app_registers_every_route_the_heartbeat_block_resolves(self) -> None:
        """`_base.html` resolves these three names unguarded, so a missing one is a
        render-time failure on every logged-in page. The guarantee is
        `arena/main.py`'s unconditional `include_router`, and this test is what keeps
        it from silently becoming conditional -- otherwise the heartbeat, and with it
        every open page's session keepalive, would vanish.
        """
        assert arena_app.url_path_for("arena_presence_heartbeat")
        assert arena_app.url_path_for("arena_presence_status")
        assert arena_app.url_path_for("static_shared_js", path="noca-presence.js")

    def test_config_raises_when_the_presence_routes_are_absent(self) -> None:
        """An application missing the presence routes must fail loudly, not render without them.

        This used to degrade to ``None`` so that stub applications mounting a
        subset of the routers could still render ``_base.html``. Every test
        application now goes through `tests/arena/conftest.py`, so the
        degradation has no remaining caller -- and silence here would mean an
        open page quietly losing its session keepalive.
        """
        app = FastAPI()

        with pytest.raises(NoMatchFound):
            heartbeat_config(_request_for(app))


class TestKeepaliveRendersOnAnAuthenticatedPage:
    """The template binding itself, which the unit tests above cannot reach.

    Every other test here calls `heartbeat_config` directly. That pins the
    configuration but says nothing about `_base.html` actually emitting it, so
    deleting the block -- or renaming the variable it sets -- would leave a page
    with no keepalive and no failing test. This renders a real authenticated page
    and asserts the element the browser needs is on it.
    """

    @pytest.mark.asyncio
    async def test_an_authenticated_page_carries_the_heartbeat_element(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A logged-in Arena page ships the config element and the shared script."""
        monkeypatch.setattr(arena_settings, "PRESENCE_ENABLED", True)
        monkeypatch.setattr(arena_settings, "PRESENCE_HEARTBEAT_SECONDS", 30)
        app = build_admin_app(session)
        user = await create_user(
            session,
            email="keepalive-admin@test.example",
            role=ArenaRole.ARENA_ADMIN,
            can_edit=True,
        )
        token = login_token(app, user)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            cookies={"arena_access_token": token},
        ) as client:
            response = await client.get("/admin/problems")

        assert response.status_code == 200
        assert "data-noca-presence" in response.text
        assert 'data-heartbeat-url="http://testserver/arena/presence/heartbeat"' in response.text
        assert 'data-status-url="http://testserver/arena/presence/status"' in response.text
        assert 'data-interval-seconds="30"' in response.text
        assert 'data-presence-enabled="true"' in response.text
        assert "noca-presence.js" in response.text

    @pytest.mark.asyncio
    async def test_the_element_survives_presence_being_disabled(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With the green dot off the page still ships the keepalive, dots disabled.

        This is the decoupling of #122 asserted where it actually matters -- on a
        rendered page rather than on the helper that feeds it.
        """
        monkeypatch.setattr(arena_settings, "PRESENCE_ENABLED", False)
        monkeypatch.setattr(arena_settings, "JWT_EXPIRE_SECONDS", 3600)
        app = build_admin_app(session)
        user = await create_user(
            session,
            email="keepalive-admin-2@test.example",
            role=ArenaRole.ARENA_ADMIN,
            can_edit=True,
        )
        token = login_token(app, user)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            cookies={"arena_access_token": token},
        ) as client:
            response = await client.get("/admin/problems")

        assert response.status_code == 200
        assert "data-noca-presence" in response.text
        assert 'data-presence-enabled="false"' in response.text
        assert 'data-interval-seconds="900"' in response.text
