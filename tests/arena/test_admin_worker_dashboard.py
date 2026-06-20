#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Arena worker-presence administration dashboard."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from arena.database import get_db
from arena.dependencies.admin import require_arena_admin
from arena.routes import admin_dashboard
from arena.routes.admin_dashboard import router
from shared.db_schema.arena import arena_worker_command_audit
from shared.enumerations import ArenaRole
from shared.services.valkey_service import WorkerClass, worker_last_jobs_key, worker_live_key, worker_registry_key
from shared.services.worker_pause_state import read_worker_pause_state

_SECRET = "test-command-secret"


class _FakeValkeyRuntime:
    """In-memory subset of ValkeyRuntime used by dashboard route tests."""

    def __init__(
        self,
        *,
        fail_transport: bool = False,
        before_publish: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        started_at = datetime(2026, 6, 11, 12, 0, tzinfo=UTC).isoformat()
        worker_record = json.dumps(
            {"started_at": started_at, "last_seen_at": started_at},
        )
        self.hashes: dict[str, dict[str, str]] = {
            worker_registry_key(WorkerClass.AUTOJUDGE): {
                "judge-online": worker_record,
                "judge-offline": worker_record,
            },
            worker_registry_key(WorkerClass.RATING): {},
            worker_registry_key(WorkerClass.AIASSISTANT): {},
            worker_last_jobs_key(WorkerClass.AUTOJUDGE): {},
            worker_last_jobs_key(WorkerClass.RATING): {},
            worker_last_jobs_key(WorkerClass.AIASSISTANT): {},
        }
        self.values = {
            worker_live_key(WorkerClass.AUTOJUDGE, "judge-online"): "online",
        }
        self.fail_transport = fail_transport
        self.before_publish = before_publish
        self.published: dict[str, str] = {}

    async def hgetall(self, key: str) -> dict[str, str]:
        """Return one registry hash."""
        return self.hashes.get(key, {})

    async def hset(self, key: str, field: str, value: str) -> None:
        """Set one hash field."""
        self.hashes.setdefault(key, {})[field] = value

    async def hmget(self, key: str, fields: list[str]) -> list[str | None]:
        """Fetch multiple hash fields."""
        h = self.hashes.get(key, {})
        return [h.get(f) for f in fields]

    async def mget(self, keys: list[str]) -> list[str | None]:
        """Return live markers."""
        return [self.values.get(key) for key in keys]

    async def get_autojudge_arena_queue_size(self) -> int:
        """Return an empty Arena autojudge queue."""
        return 0

    async def get_ai_review_queue_size(self) -> int:
        """Return an empty AI review queue."""
        return 0

    async def eval(self, script: str, numkeys: int, *args: str) -> int:
        """Apply the remove script used by the route (2- or 3-key variant)."""
        keys = args[:numkeys]
        argv = args[numkeys:]
        registry_key, live_key = keys[0], keys[1]
        worker_id = argv[0]
        self.hashes.setdefault(registry_key, {}).pop(worker_id, None)
        self.values.pop(live_key, None)
        if numkeys >= 3:
            self.hashes.setdefault(keys[2], {}).pop(worker_id, None)
        return 1

    async def delete(self, *keys: str) -> int:
        """Delete string keys."""
        for key in keys:
            self.values.pop(key, None)
        return len(keys)

    async def set_reporting(self, key: str, value: str, *, ex: int) -> bool:
        """Record a published command unless transport is configured to fail."""
        if self.before_publish is not None:
            await self.before_publish()
        if self.fail_transport:
            return False
        self.published[key] = value
        return True


async def _admin() -> SimpleNamespace:
    """Return the minimum admin object needed by dashboard templates."""
    return SimpleNamespace(
        id="admin-1",
        email="admin@example.com",
        role=ArenaRole.ARENA_ADMIN,
        can_edit=True,
        nome="Admin",
        dta_foto=None,
    )


def _build_app(
    session: Any,
    *,
    valkey: _FakeValkeyRuntime | None = None,
    authorized: bool = True,
) -> FastAPI:
    """Build a minimal app using the real dashboard fragment template."""
    from starlette.middleware.sessions import SessionMiddleware

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-session-secret")
    app.state.valkey_runtime = valkey or _FakeValkeyRuntime()

    arena_dir = Path(__file__).resolve().parents[2] / "arena"
    templates = Jinja2Templates(directory=arena_dir / "template")
    templates.env.globals["arena_format_datetime"] = lambda value, user, fmt=None: value.isoformat()
    templates.env.globals["arena_format_relative_datetime"] = lambda value: "5 minutes ago"
    setup_flash(templates)
    app.state.arena_templates = templates
    app.include_router(router)

    async def _get_db_override() -> Any:
        yield session

    app.dependency_overrides[get_db] = _get_db_override

    if authorized:
        app.dependency_overrides[require_arena_admin] = _admin
    else:

        async def _reject() -> None:
            raise HTTPException(status_code=403)

        app.dependency_overrides[require_arena_admin] = _reject
    return app


@pytest.mark.asyncio
async def test_worker_fragment_groups_status_and_polls_with_htmx(session) -> None:
    """Render three cards with online/offline rows and ten-second polling."""
    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/workers")

    assert response.status_code == 200
    assert response.text.count('class="arena-card h-100"') == 3
    assert response.text.count('class="col-12"') == 3
    assert "col-xl-4" not in response.text
    assert 'hx-trigger="every 10s"' in response.text
    assert "judge-online" in response.text
    assert "judge-offline" in response.text
    assert 'aria-label="Online"' in response.text
    assert 'aria-label="Offline"' in response.text
    assert "5 minutes ago" in response.text
    assert "Last start" in response.text
    assert "Last seen" in response.text
    assert 'class="d-block text-muted"' in response.text
    assert response.text.count("No workers seen.") == 2


@pytest.mark.asyncio
async def test_htmx_remove_returns_refreshed_fragment(session) -> None:
    """Remove a worker and immediately return the updated card fragment."""
    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/admin/dashboard/workers/remove",
            headers={"HX-Request": "true"},
            data={"worker_class": "autojudge", "worker_id": "judge-offline"},
        )

    assert response.status_code == 200
    assert "judge-offline" not in response.text
    assert "judge-online" in response.text


@pytest.mark.asyncio
async def test_dashboard_routes_require_arena_admin(session) -> None:
    """Reject non-admin access to page, fragment, remove action, and AI credits."""
    app = _build_app(session, authorized=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = [
            await client.get("/admin/dashboard"),
            await client.get("/admin/dashboard/workers"),
            await client.post(
                "/admin/dashboard/workers/remove",
                data={"worker_class": "rating", "worker_id": "rating-1"},
            ),
            await client.post(
                "/admin/dashboard/workers/pause",
                data={"worker_class": "autojudge", "worker_id": "judge-online"},
            ),
            await client.get("/admin/dashboard/service-status"),
            await client.get("/admin/dashboard/ai-credits"),
        ]

    assert [response.status_code for response in responses] == [403, 403, 403, 403, 403, 403]


@pytest.mark.asyncio
async def test_dashboard_index_renders_landing_template(session) -> None:
    """Landing page renders admin/dashboard.html without worker_cards."""
    app = _build_app(session)
    rendered: dict[str, Any] = {}

    class _Templates:
        def TemplateResponse(
            self,
            request: object,
            name: str,
            context: dict[str, object],
        ) -> HTMLResponse:
            rendered.update({"name": name, "context": context})
            return HTMLResponse("dashboard")

    app.state.arena_templates = _Templates()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard")

    assert response.status_code == 200
    assert rendered["name"] == "admin/dashboard.html"
    assert "worker_cards" not in rendered["context"]


@pytest.mark.asyncio
async def test_service_status_page_renders_worker_cards(session) -> None:
    """Service Status sub-page renders admin/dashboard_service_status.html with worker_cards."""
    app = _build_app(session)
    rendered: dict[str, Any] = {}

    class _Templates:
        def TemplateResponse(
            self,
            request: object,
            name: str,
            context: dict[str, object],
        ) -> HTMLResponse:
            rendered.update({"name": name, "context": context})
            return HTMLResponse("service status")

    app.state.arena_templates = _Templates()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/service-status")

    assert response.status_code == 200
    assert rendered["name"] == "admin/dashboard_service_status.html"
    assert len(rendered["context"]["worker_cards"]) == 3  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_pause_commits_pg_and_audit_before_publishing(session, engine, monkeypatch) -> None:
    """Pausing commits authoritative state and a delivered audit row, then publishes."""
    monkeypatch.setattr(admin_dashboard.settings, "WORKER_COMMAND_SECRET", _SECRET)
    observed_before_publish: list[tuple[bool, str]] = []

    async def _observe_committed_state() -> None:
        async with engine.connect() as conn:
            state = await read_worker_pause_state(conn, "autojudge", "judge-online")
            audit = (
                (
                    await conn.execute(
                        select(arena_worker_command_audit).where(
                            arena_worker_command_audit.c.worker_class == "autojudge",
                            arena_worker_command_audit.c.worker_id == "judge-online",
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert state is not None
        observed_before_publish.append((state.paused, audit["transport_status"]))

    valkey = _FakeValkeyRuntime(before_publish=_observe_committed_state)
    app = _build_app(session, valkey=valkey)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/admin/dashboard/workers/pause",
            headers={"HX-Request": "true"},
            data={"worker_class": "autojudge", "worker_id": "judge-online"},
        )

    assert response.status_code == 200
    assert "Paused" in response.text

    state = await read_worker_pause_state(session, "autojudge", "judge-online")
    assert state is not None and state.paused is True and state.generation == 1

    audit = (await session.execute(select(arena_worker_command_audit))).all()
    assert len(audit) == 1
    row = audit[0]._mapping[arena_worker_command_audit.c.id] is not None  # row exists
    assert row
    committed = audit[0]._mapping
    assert committed[arena_worker_command_audit.c.outcome] == "committed"
    assert committed[arena_worker_command_audit.c.transport_status] == "delivered"
    assert observed_before_publish == [(True, "pending")]
    assert valkey.published  # a command was published


@pytest.mark.asyncio
async def test_resume_after_pause(session, monkeypatch) -> None:
    """Resuming clears the paused state and bumps the generation."""
    monkeypatch.setattr(admin_dashboard.settings, "WORKER_COMMAND_SECRET", _SECRET)
    app = _build_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/admin/dashboard/workers/pause",
            data={"worker_class": "autojudge", "worker_id": "judge-online"},
        )
        await client.post(
            "/admin/dashboard/workers/resume",
            data={"worker_class": "autojudge", "worker_id": "judge-online"},
        )

    state = await read_worker_pause_state(session, "autojudge", "judge-online")
    assert state is not None
    assert state.paused is False
    assert state.generation == 2


@pytest.mark.asyncio
async def test_transport_failure_still_succeeds(session, monkeypatch) -> None:
    """A failed publish still commits state and marks the audit transport_failed."""
    monkeypatch.setattr(admin_dashboard.settings, "WORKER_COMMAND_SECRET", _SECRET)
    valkey = _FakeValkeyRuntime(fail_transport=True)
    app = _build_app(session, valkey=valkey)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/admin/dashboard/workers/pause",
            data={"worker_class": "autojudge", "worker_id": "judge-online"},
        )

    assert response.status_code in (200, 303)
    state = await read_worker_pause_state(session, "autojudge", "judge-online")
    assert state is not None and state.paused is True

    audit = (await session.execute(select(arena_worker_command_audit))).all()
    assert audit[0]._mapping[arena_worker_command_audit.c.outcome] == "committed"
    assert audit[0]._mapping[arena_worker_command_audit.c.transport_status] == "transport_failed"


@pytest.mark.asyncio
async def test_secret_unset_rejects_and_hides_buttons(session, monkeypatch) -> None:
    """With no secret, pause is rejected_disabled and the fragment hides controls."""
    monkeypatch.setattr(admin_dashboard.settings, "WORKER_COMMAND_SECRET", "")
    app = _build_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        fragment = await client.get("/admin/dashboard/workers")
        paused = await client.post(
            "/admin/dashboard/workers/pause",
            data={"worker_class": "autojudge", "worker_id": "judge-online"},
        )

    assert "Pause worker" not in fragment.text
    # The operation is rejected and writes a rejected_disabled audit row.
    audit = (await session.execute(select(arena_worker_command_audit))).all()
    assert audit[0]._mapping[arena_worker_command_audit.c.outcome] == "rejected_disabled"
    state = await read_worker_pause_state(session, "autojudge", "judge-online")
    assert state is None
    assert paused.status_code in (200, 303)


@pytest.mark.asyncio
async def test_rating_class_rejected_bad_request(session, monkeypatch) -> None:
    """Pausing the always-on rating worker yields HTTP 400 and an audit row."""
    monkeypatch.setattr(admin_dashboard.settings, "WORKER_COMMAND_SECRET", _SECRET)
    app = _build_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/admin/dashboard/workers/pause",
            data={"worker_class": "rating", "worker_id": "rating-1"},
        )

    assert response.status_code == 400
    audit = (await session.execute(select(arena_worker_command_audit))).all()
    assert audit[0]._mapping[arena_worker_command_audit.c.outcome] == "rejected_bad_request"


@pytest.mark.asyncio
async def test_unknown_worker_class_is_audited_before_http_400(session, monkeypatch) -> None:
    """An unknown worker class is recorded as rejected_bad_request."""
    monkeypatch.setattr(admin_dashboard.settings, "WORKER_COMMAND_SECRET", _SECRET)
    app = _build_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/admin/dashboard/workers/pause",
            data={"worker_class": "unknown-worker", "worker_id": "unknown-1"},
        )

    assert response.status_code == 400
    audit = (await session.execute(select(arena_worker_command_audit))).mappings().one()
    assert audit["worker_class"] == "unknown-worker"
    assert audit["outcome"] == "rejected_bad_request"
    assert audit["transport_status"] == "n_a"


@pytest.mark.asyncio
async def test_last_job_column_shows_relative_and_absolute_when_set(session) -> None:
    """A worker with a last_job_at timestamp renders relative and absolute times."""
    last_job_iso = datetime(2026, 6, 11, 14, 30, tzinfo=UTC).isoformat()
    valkey = _FakeValkeyRuntime()
    valkey.hashes[worker_last_jobs_key(WorkerClass.AUTOJUDGE)]["judge-online"] = last_job_iso
    app = _build_app(session, valkey=valkey)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/workers")

    assert response.status_code == 200
    assert "5 minutes ago" in response.text
    assert "Last job" in response.text
    # The absolute timestamp comes from arena_format_datetime which uses isoformat in tests.
    assert last_job_iso in response.text


@pytest.mark.asyncio
async def test_last_job_column_shows_no_jobs_yet_when_absent(session) -> None:
    """A worker with no last_job_at renders the 'No jobs yet' placeholder."""
    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/workers")

    assert response.status_code == 200
    assert "No jobs yet" in response.text


@pytest.mark.asyncio
async def test_remove_clears_last_job_entry(session) -> None:
    """Removing a worker via the dashboard also clears its last-job hash field."""
    last_job_iso = datetime(2026, 6, 11, 14, 30, tzinfo=UTC).isoformat()
    valkey = _FakeValkeyRuntime()
    valkey.hashes[worker_last_jobs_key(WorkerClass.AUTOJUDGE)]["judge-offline"] = last_job_iso
    app = _build_app(session, valkey=valkey)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/admin/dashboard/workers/remove",
            data={"worker_class": "autojudge", "worker_id": "judge-offline"},
        )

    assert "judge-offline" not in valkey.hashes[worker_last_jobs_key(WorkerClass.AUTOJUDGE)]


@pytest.mark.asyncio
async def test_list_worker_cards_passes_last_job_at_through(session) -> None:
    """list_worker_cards() propagates last_job_at from WorkerPresence to WorkerRow."""
    from arena.services.admin_worker_service import list_worker_cards

    last_job_iso = datetime(2026, 6, 11, 14, 30, tzinfo=UTC).isoformat()
    valkey = _FakeValkeyRuntime()
    valkey.hashes[worker_last_jobs_key(WorkerClass.AUTOJUDGE)]["judge-online"] = last_job_iso
    cards = await list_worker_cards(session, valkey, secret=_SECRET)

    autojudge_card = next(c for c in cards if c.worker_class == WorkerClass.AUTOJUDGE)
    online_row = next(r for r in autojudge_card.workers if r.worker_id == "judge-online")
    offline_row = next(r for r in autojudge_card.workers if r.worker_id == "judge-offline")

    assert online_row.last_job_at is not None
    assert online_row.last_job_at.isoformat() == last_job_iso
    assert offline_row.last_job_at is None
