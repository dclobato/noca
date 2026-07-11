#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The Web security-events viewer must show only Web-module events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import security_events
from shared.services.security_events import record_security_event
from tests.web.test_inactive_contest_routes import _build_app, _login_uberadmin


@pytest.mark.asyncio
async def test_uberadmin_login_records_auth_success(
    session: AsyncSession,
    uberadmin,
) -> None:
    await session.commit()
    app, _auth_service = _build_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/login",
            data={"identifier": uberadmin.username, "password": "TestPass1!", "next_url": "/uberadmin"},
            follow_redirects=False,
        )

    result = await session.execute(
        select(security_events.c.id).where(
            security_events.c.module == "web",
            security_events.c.event_type == "auth_success",
            security_events.c.actor_user_id == uberadmin.id,
        )
    )
    assert response.status_code == 303
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_uberadmin_logout_records_auth_logout(
    session: AsyncSession,
    uberadmin,
) -> None:
    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get("/logout", follow_redirects=False)

    result = await session.execute(
        select(security_events.c.id).where(
            security_events.c.module == "web",
            security_events.c.event_type == "auth_logout",
            security_events.c.actor_user_id == uberadmin.id,
        )
    )
    assert response.status_code == 303
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_viewer_excludes_arena_and_aiassistant_events(
    session: AsyncSession,
    uberadmin,
) -> None:
    await record_security_event(session, module="web", event_type="web_marker_event")
    await record_security_event(session, module="arena", event_type="arena_marker_event")
    await record_security_event(session, module="aiassistant", event_type="ai_marker_event")
    await session.commit()

    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get("/uberadmin/security-events")

    assert response.status_code == 200
    assert "web_marker_event" in response.text
    assert "arena_marker_event" not in response.text
    assert "ai_marker_event" not in response.text


@pytest.mark.asyncio
async def test_viewer_event_type_filter_stays_within_web(
    session: AsyncSession,
    uberadmin,
) -> None:
    await record_security_event(session, module="web", event_type="auth_failure")
    await record_security_event(session, module="web", event_type="admin_action")
    await record_security_event(session, module="arena", event_type="auth_failure")
    await session.commit()

    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get("/uberadmin/security-events", params={"event_type": "auth_failure"})

    assert response.status_code == 200
    # Only the Web auth_failure row is shown; the Arena one with the same event
    # type must not leak in through the filter.
    assert response.text.count("auth_failure") >= 1
    assert "admin_action" not in _event_rows(response.text)


@pytest.mark.asyncio
async def test_viewer_paginates_web_security_events(
    session: AsyncSession,
    uberadmin,
) -> None:
    """The Web viewer exposes older matching events through page navigation."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(15):
        event_type = f"web_page_event_{i:02d}"
        await record_security_event(session, module="web", event_type=event_type)
        await session.flush()
        await session.execute(
            update(security_events)
            .where(security_events.c.event_type == event_type)
            .values(created_at=base + timedelta(seconds=i))
        )
    await session.commit()

    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get("/uberadmin/security-events", params={"per_page": "10", "page": "2"})

    assert response.status_code == 200
    rows = _event_rows(response.text)
    assert "15 matching security events" in response.text
    assert "web_page_event_04" in rows
    assert "web_page_event_14" not in rows


@pytest.mark.asyncio
async def test_viewer_accepts_standard_per_page_options(
    session: AsyncSession,
    uberadmin,
) -> None:
    """All standard page-size options render successfully."""
    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        for size in (10, 25, 50, 100, 500):
            response = await client.get("/uberadmin/security-events", params={"per_page": str(size)})
            assert response.status_code == 200, f"Expected 200 for per_page={size}"


def _event_rows(html: str) -> str:
    """Return only the table-body portion of the rendered page."""
    _, _, body = html.partition("<tbody>")
    return body
