#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the authenticated Arena worker-status page."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from httpx import ASGITransport, AsyncClient

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.routes import status
from arena.routes.status import router
from arena.services.admin_worker_service import (
    WorkerAggregateState,
    WorkerCard,
    WorkerRow,
    aggregate_worker_statuses,
)
from arena.services.valkey_service import WorkerClass


def _card(
    worker_class: WorkerClass,
    *,
    online: bool,
    paused: bool = False,
) -> WorkerCard:
    """Build one worker card for aggregate-status tests."""
    workers = []
    if online or paused:
        workers.append(
            WorkerRow(
                worker_id=f"{worker_class.value}-1",
                online=online,
                started_at=datetime(2026, 6, 12, tzinfo=UTC),
                last_seen_at=datetime(2026, 6, 12, tzinfo=UTC),
                paused=paused,
                paused_by=None,
                last_job_at=None,
            )
        )
    return WorkerCard(
        worker_class=worker_class,
        title=f"{worker_class.value} workers",
        icon="settings",
        workers=workers,
        supports_pause=worker_class != WorkerClass.RATING,
        pause_enabled=True,
        pending_tasks=None,
    )


def test_aggregate_worker_statuses_requires_online_unpaused_worker() -> None:
    """Mark a class available only when at least one worker consumes jobs."""
    cards = [
        _card(WorkerClass.AUTOJUDGE, online=True),
        _card(WorkerClass.RATING, online=False),
        _card(WorkerClass.AIASSISTANT, online=True, paused=True),
    ]

    states = {item.worker_class: item.state for item in aggregate_worker_statuses(cards)}

    assert states == {
        WorkerClass.AUTOJUDGE: WorkerAggregateState.AVAILABLE,
        WorkerClass.RATING: WorkerAggregateState.UNAVAILABLE,
        WorkerClass.AIASSISTANT: WorkerAggregateState.UNAVAILABLE,
    }


def _build_app(*, authenticated: bool = True) -> tuple[FastAPI, dict[str, Any]]:
    """Build a minimal status-route application."""
    app = FastAPI()
    app.state.valkey_runtime = object()
    rendered: dict[str, Any] = {}

    class _Templates:
        """Capture template rendering arguments."""

        def TemplateResponse(
            self,
            request: object,
            name: str,
            context: dict[str, object],
        ) -> HTMLResponse:
            """Return a response while recording the selected template."""
            rendered.update({"name": name, "context": context})
            return HTMLResponse("status")

    app.state.arena_templates = _Templates()

    @app.get("/auth/login", name="arena_login")
    async def _login() -> Response:
        """Provide the named login route used by redirects."""
        return Response("login")

    app.include_router(router)

    async def _get_db_override() -> Any:
        yield object()

    async def _current_user_override() -> SimpleNamespace | None:
        if not authenticated:
            return None
        return SimpleNamespace(id="user-1", email="user@example.com")

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_arena_user] = _current_user_override
    return app, rendered


@pytest.mark.asyncio
async def test_status_page_renders_aggregate_worker_states(monkeypatch) -> None:
    """Render the status template with all aggregate states."""
    app, rendered = _build_app()
    cards = [
        _card(WorkerClass.AUTOJUDGE, online=True),
        _card(WorkerClass.RATING, online=False),
        _card(WorkerClass.AIASSISTANT, online=True, paused=True),
    ]

    async def _list_worker_cards(*args: object, **kwargs: object) -> list[WorkerCard]:
        return cards

    monkeypatch.setattr(status.admin_worker_service, "list_worker_cards", _list_worker_cards)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/status")

    assert response.status_code == 200
    assert rendered["name"] == "status.html"
    worker_statuses = rendered["context"]["worker_statuses"]
    assert [item.state for item in worker_statuses] == [
        WorkerAggregateState.AVAILABLE,
        WorkerAggregateState.UNAVAILABLE,
        WorkerAggregateState.UNAVAILABLE,
    ]
    assert [item.title for item in worker_statuses] == [
        "AutoJudge",
        "Rating",
        "AI Assistant",
    ]


@pytest.mark.asyncio
async def test_status_page_renders_unknown_when_retrieval_fails(monkeypatch) -> None:
    """Return HTTP 200 with gray-ready unknown states after backend failure."""
    app, rendered = _build_app()

    async def _fail(*args: object, **kwargs: object) -> list[WorkerCard]:
        raise RuntimeError("status backend unavailable")

    monkeypatch.setattr(status.admin_worker_service, "list_worker_cards", _fail)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/status")

    assert response.status_code == 200
    worker_statuses = rendered["context"]["worker_statuses"]
    assert {item.state for item in worker_statuses} == {
        WorkerAggregateState.UNKNOWN,
    }


@pytest.mark.asyncio
async def test_status_page_redirects_guests_to_login() -> None:
    """Redirect guests to login while preserving the status URL."""
    app, _ = _build_app(authenticated=False)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        response = await client.get("/status")

    assert response.status_code == 303
    assert response.headers["location"] == "http://test/auth/login?next=%2Fstatus"


def test_status_template_and_footer_expose_accessible_states() -> None:
    """Keep all status colors labeled and place Status before Contact."""
    arena_dir = Path(__file__).resolve().parents[2] / "arena"
    status_template = (arena_dir / "template" / "status.html").read_text()
    footer_template = (arena_dir / "template" / "_partials" / "_footer.html").read_text()

    assert 'class="text-success' in status_template
    assert 'class="text-danger' in status_template
    assert 'class="text-secondary' in status_template
    assert "Available</span>" in status_template
    assert "Unavailable</span>" in status_template
    assert "Unknown</span>" in status_template
    assert footer_template.index('url_for("arena_status")') < footer_template.index(">Contact<")
