#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Arena admin security-events page."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from shared.db_schema import security_events
from shared.services.security_events import record_security_event
from shared.services.security_events_export import CSV_HEADER
from tests.arena.test_admin_login_history_global import _build_app


@pytest.mark.asyncio
async def test_security_events_route_renders_recent_events(session: AsyncSession) -> None:
    """GET /admin/dashboard/security-events renders persisted events for admins."""
    await record_security_event(
        session,
        module="arena",
        event_type="auth_throttle_lockout",
        severity="warning",
        client_ip="203.0.113.9",
        source_port=54321,
        request_id="11111111-2222-3333-4444-555555555555",
        metadata={"action": "login"},
    )
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/security-events")

    assert response.status_code == 200
    assert "Security Events" in response.text
    assert "auth_throttle_lockout" in response.text
    assert "54321" in response.text
    assert "11111111-2222-3333-4444-555555555555" in response.text


@pytest.mark.asyncio
async def test_security_events_route_scopes_to_arena_and_aiassistant(session: AsyncSession) -> None:
    """The Arena viewer shows arena and aiassistant events but not web events."""
    await record_security_event(session, module="arena", event_type="arena_marker_event")
    await record_security_event(session, module="aiassistant", event_type="ai_marker_event")
    await record_security_event(session, module="web", event_type="web_marker_event")
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/security-events")

    assert response.status_code == 200
    assert "arena_marker_event" in response.text
    assert "ai_marker_event" in response.text
    assert "web_marker_event" not in response.text


@pytest.mark.asyncio
async def test_security_events_route_filters_by_module(session: AsyncSession) -> None:
    """The Arena security-events page can filter to one owned module."""
    await record_security_event(session, module="arena", event_type="arena_marker_event")
    await record_security_event(session, module="aiassistant", event_type="ai_marker_event")
    await record_security_event(session, module="web", event_type="web_marker_event")
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/security-events", params={"module": "aiassistant"})

    assert response.status_code == 200
    assert "ai_marker_event" in response.text
    assert "arena_marker_event" not in _event_rows(response.text)
    assert "web_marker_event" not in response.text


@pytest.mark.asyncio
async def test_security_events_route_invalid_module_uses_owned_scope(session: AsyncSession) -> None:
    """Invalid module filters do not leak Web events or break the page."""
    await record_security_event(session, module="arena", event_type="arena_marker_event")
    await record_security_event(session, module="aiassistant", event_type="ai_marker_event")
    await record_security_event(session, module="web", event_type="web_marker_event")
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/security-events", params={"module": "web"})

    assert response.status_code == 200
    assert "arena_marker_event" in response.text
    assert "ai_marker_event" in response.text
    assert "web_marker_event" not in response.text


@pytest.mark.asyncio
async def test_security_events_route_filters_by_event_type(session: AsyncSession) -> None:
    """The Arena security-events page can filter by event type."""
    await record_security_event(session, module="arena", event_type="auth_failure")
    await record_security_event(session, module="arena", event_type="admin_action")
    await record_security_event(session, module="aiassistant", event_type="auth_failure")
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/security-events", params={"event_type": "auth_failure"})

    assert response.status_code == 200
    assert "auth_failure" in response.text
    assert "admin_action" not in _event_rows(response.text)


@pytest.mark.asyncio
async def test_security_events_route_paginates_events(session: AsyncSession) -> None:
    """Older Arena-owned security events are available on later pages."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(15):
        event_type = f"arena_page_event_{i:02d}"
        await record_security_event(session, module="arena", event_type=event_type)
        await session.flush()
        await session.execute(
            update(security_events)
            .where(security_events.c.event_type == event_type)
            .values(created_at=base + timedelta(seconds=i))
        )
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/admin/dashboard/security-events",
            params={"per_page": "10", "page": "2"},
        )

    assert response.status_code == 200
    rows = _event_rows(response.text)
    assert "15 security events" in response.text
    assert "arena_page_event_04" in rows
    assert "arena_page_event_14" not in rows


@pytest.mark.asyncio
async def test_security_events_route_accepts_all_valid_per_page_sizes(session: AsyncSession) -> None:
    """All allowed security-event per-page values return 200."""
    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for size in (10, 25, 50, 100, 500):
            response = await client.get("/admin/dashboard/security-events", params={"per_page": str(size)})
            assert response.status_code == 200, f"Expected 200 for per_page={size}"


def _event_rows(html: str) -> str:
    """Return only the table-body portion of the rendered page."""
    _, _, body = html.partition("<tbody>")
    return body


@pytest.mark.asyncio
async def test_security_events_csv_exports_all_rows_ignoring_filters(session: AsyncSession) -> None:
    """The CSV download returns every Arena-scoped row, not the filtered page."""
    for i in range(3):
        await record_security_event(session, module="arena", event_type=f"arena_csv_event_{i}")
    await record_security_event(session, module="aiassistant", event_type="ai_csv_event")
    await record_security_event(session, module="web", event_type="web_csv_event")
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/admin/dashboard/security-events.csv",
            params={"module": "arena", "event_type": "arena_csv_event_0", "per_page": "10"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in response.headers["content-disposition"]
    body = response.text
    assert body.startswith("﻿")
    rows = list(csv.reader(io.StringIO(body.lstrip("﻿"))))
    assert rows[0] == list(CSV_HEADER)
    exported = {row[2] for row in rows[1:]}
    assert {"arena_csv_event_0", "arena_csv_event_1", "arena_csv_event_2", "ai_csv_event"} <= exported
    assert "web_csv_event" not in exported


@pytest.mark.asyncio
async def test_security_events_csv_opens_exactly_one_session(session: AsyncSession) -> None:
    """The stream reads through the dependency's session; it must not open a second (#198)."""
    app = _build_app(session)
    session_factory = app.state.arena_db_session
    opened = 0

    def counted_session_factory() -> AsyncSession:
        nonlocal opened
        opened += 1
        return session_factory()

    app.state.arena_db_session = counted_session_factory

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/security-events.csv")

    assert response.status_code == 200
    # The dependency's session comes from the factory unless the harness injects
    # it through an override; either way, a second count is a nested session.
    assert opened == (0 if get_db in app.dependency_overrides else 1)


@pytest.mark.asyncio
async def test_security_events_csv_requires_admin(session: AsyncSession) -> None:
    """A non-admin cannot download the Arena security-event export."""
    app = _build_app(session, authorized=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/security-events.csv")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_security_events_csv_neutralizes_formula_cells(session: AsyncSession) -> None:
    """A user agent that looks like a formula is escaped as text."""
    await record_security_event(
        session,
        module="arena",
        event_type="arena_csv_formula",
        user_agent="=cmd|'/c calc'!A1",
        metadata={"note": "line one\nline two"},
    )
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/security-events.csv")

    rows = list(csv.reader(io.StringIO(response.text.lstrip("﻿"))))
    row = next(row for row in rows[1:] if row[2] == "arena_csv_formula")
    assert row[10].startswith("'=")
    assert "\n" not in row[11]
    assert "line one" in row[11]
