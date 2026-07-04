#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the shared security-event log service and admin-action audit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from shared.db_schema import security_events
from shared.services.admin_audit import ADMIN_ACTION_EVENT_TYPE, record_admin_action
from shared.services.security_events import (
    delete_security_events_older_than,
    list_recent_security_events,
    list_security_event_filter_values,
    list_security_events_paginated,
    record_request_security_event,
    record_security_event,
)


def _request(client_ip: str = "203.0.113.9", user_agent: str = "pytest-agent") -> SimpleNamespace:
    """Build a minimal request-like object."""
    return SimpleNamespace(
        client=SimpleNamespace(host=client_ip),
        headers={"User-Agent": user_agent},
    )


@pytest.mark.asyncio
async def test_record_and_list_security_event(session) -> None:
    await record_security_event(
        session,
        module="arena",
        event_type="auth_failure",
        severity="info",
        client_ip="203.0.113.9",
        metadata={"action": "login"},
    )
    await session.commit()

    rows = await list_recent_security_events(session)
    assert len(rows) == 1
    assert rows[0].module == "arena"
    assert rows[0].event_type == "auth_failure"
    assert rows[0].metadata == {"action": "login"}


@pytest.mark.asyncio
async def test_list_filters_by_module_and_event_type(session) -> None:
    await record_security_event(session, module="arena", event_type="auth_failure")
    await record_security_event(session, module="web", event_type="auth_failure")
    await record_security_event(session, module="web", event_type="admin_action")
    await session.commit()

    web_only = await list_recent_security_events(session, module="web")
    assert {row.module for row in web_only} == {"web"}

    admin_only = await list_recent_security_events(session, event_type="admin_action")
    assert [row.event_type for row in admin_only] == ["admin_action"]

    filters = await list_security_event_filter_values(session)
    assert filters.modules == ["arena", "web"]
    assert "admin_action" in filters.event_types


@pytest.mark.asyncio
async def test_list_security_events_paginated_returns_all_matching_rows(session) -> None:
    """Paginated listing is not capped to the old recent-event limit."""
    for i in range(205):
        await record_security_event(session, module="web", event_type=f"event_{i:03d}")
    await record_security_event(session, module="arena", event_type="arena_event")
    await session.commit()

    first_page = await list_security_events_paginated(
        session,
        page=1,
        per_page=100,
        module="web",
    )
    third_page = await list_security_events_paginated(
        session,
        page=3,
        per_page=100,
        module="web",
    )

    assert first_page.total == 205
    assert len(first_page.items) == 100
    assert third_page.page == 3
    assert len(third_page.items) == 5
    assert {row.module for row in third_page.items} == {"web"}


@pytest.mark.asyncio
async def test_list_security_events_paginated_filters_and_clamps_page(session) -> None:
    """Pagination applies filters to count and rows, then clamps invalid pages."""
    await record_security_event(session, module="arena", event_type="auth_failure")
    await record_security_event(session, module="aiassistant", event_type="auth_failure")
    await record_security_event(session, module="arena", event_type="admin_action")
    await record_security_event(session, module="web", event_type="auth_failure")
    await session.commit()

    result = await list_security_events_paginated(
        session,
        page=99,
        per_page=1,
        modules=["arena", "aiassistant"],
        event_type="auth_failure",
    )
    empty_modules = await list_security_events_paginated(
        session,
        page=1,
        per_page=25,
        modules=[],
    )

    assert result.total == 2
    assert result.page == 2
    assert len(result.items) == 1
    assert result.items[0].event_type == "auth_failure"
    assert empty_modules.total == 0
    assert empty_modules.items == []


@pytest.mark.asyncio
async def test_record_admin_action_writes_structured_metadata(session) -> None:
    await record_admin_action(
        session,
        _request(),
        module="web",
        actor_user_id="admin-1",
        action="delete",
        target_type="contest_user",
        target_id="user-9",
        detail="contest=demo",
    )
    await session.commit()

    rows = await list_recent_security_events(session, event_type=ADMIN_ACTION_EVENT_TYPE)
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_user_id == "admin-1"
    assert row.client_ip == "203.0.113.9"
    assert row.user_agent == "pytest-agent"
    assert row.metadata == {
        "action": "delete",
        "target_type": "contest_user",
        "target_id": "user-9",
        "detail": "contest=demo",
    }


@pytest.mark.asyncio
async def test_record_request_security_event_uses_request_metadata(session) -> None:
    await record_request_security_event(
        session,
        _request(client_ip="198.51.100.5", user_agent="agent-x"),
        module="arena",
        event_type="signup_existing_account",
    )
    await session.commit()

    rows = await list_recent_security_events(session)
    assert rows[0].client_ip == "198.51.100.5"
    assert rows[0].user_agent == "agent-x"


@pytest.mark.asyncio
async def test_delete_security_events_older_than_retention(session) -> None:
    await record_security_event(session, module="arena", event_type="old")
    await record_security_event(session, module="arena", event_type="fresh")
    await session.commit()

    old_cutoff = datetime.now(UTC) - timedelta(days=400)
    await session.execute(
        update(security_events).where(security_events.c.event_type == "old").values(created_at=old_cutoff)
    )
    await session.commit()

    deleted = await delete_security_events_older_than(session, retention_days=180)
    await session.commit()
    assert deleted == 1

    remaining = await list_recent_security_events(session)
    assert [row.event_type for row in remaining] == ["fresh"]


@pytest.mark.asyncio
async def test_delete_security_events_disabled_when_retention_zero(session) -> None:
    await record_security_event(session, module="arena", event_type="keep")
    await session.commit()

    deleted = await delete_security_events_older_than(session, retention_days=0)
    await session.commit()
    assert deleted == 0
    assert len(await list_recent_security_events(session)) == 1


async def _age_all_events(session) -> None:
    """Push every stored event past a 400-day age so retention deletes them."""
    old_cutoff = datetime.now(UTC) - timedelta(days=400)
    await session.execute(update(security_events).values(created_at=old_cutoff))
    await session.commit()


@pytest.mark.asyncio
async def test_delete_only_touches_owned_modules(session) -> None:
    await record_security_event(session, module="web", event_type="e")
    await record_security_event(session, module="arena", event_type="e")
    await record_security_event(session, module="aiassistant", event_type="e")
    await session.commit()
    await _age_all_events(session)

    # The Web runtime prunes only its own module.
    deleted_web = await delete_security_events_older_than(session, retention_days=180, modules=["web"])
    await session.commit()
    assert deleted_web == 1
    assert {row.module for row in await list_recent_security_events(session)} == {"arena", "aiassistant"}

    # The Arena runtime owns both arena and the co-deployed aiassistant worker.
    deleted_arena = await delete_security_events_older_than(
        session, retention_days=180, modules=["arena", "aiassistant"]
    )
    await session.commit()
    assert deleted_arena == 2
    assert await list_recent_security_events(session) == []


@pytest.mark.asyncio
async def test_delete_with_empty_modules_deletes_nothing(session) -> None:
    await record_security_event(session, module="web", event_type="e")
    await session.commit()
    await _age_all_events(session)

    deleted = await delete_security_events_older_than(session, retention_days=180, modules=[])
    await session.commit()
    assert deleted == 0
    assert len(await list_recent_security_events(session)) == 1
