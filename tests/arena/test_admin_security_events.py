#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Arena admin security-events page."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import security_events
from shared.services.security_events import record_security_event
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
        metadata={"action": "login"},
    )
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/security-events")

    assert response.status_code == 200
    assert "Security Events" in response.text
    assert "auth_throttle_lockout" in response.text


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
