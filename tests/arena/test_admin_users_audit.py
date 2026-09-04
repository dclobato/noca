#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Admin audit coverage for the two personal-data routes that recorded nothing.

``admin_user_change_date_of_birth`` and ``admin_user_change_personal_info`` mutate the
data the age shield is computed from -- a date of birth decides whether an account is
shielded at all -- yet neither wrote an ``admin_action`` row, unlike every sibling that
touches an account. These tests pin the rows now that they do.
"""

from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_users  # noqa: F401
from shared.db_schema import security_events
from shared.enumerations import ArenaRole
from tests.arena.test_admin_users import (
    _build_admin_app,
    _create_arena_user,
    _login_token,
)


async def _audit_rows(session: AsyncSession) -> list[dict[str, str]]:
    """Return the metadata payload of every recorded Arena admin action."""
    result = await session.execute(
        select(security_events.c.metadata)
        .where(
            security_events.c.module == "arena",
            security_events.c.event_type == "admin_action",
        )
        .order_by(security_events.c.id)
    )
    return [dict(payload or {}) for payload in result.scalars()]


@pytest.mark.asyncio
async def test_changing_a_date_of_birth_is_audited(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/date-of-birth",
            data={"date_of_birth": "2012-05-04"},
            follow_redirects=False,
        )

    await session.refresh(target)
    assert response.status_code == 303
    assert target.dta_nascimento == date(2012, 5, 4)
    actions = [row.get("action") for row in await _audit_rows(session)]
    assert "change_date_of_birth" in actions


@pytest.mark.asyncio
async def test_a_rejected_date_of_birth_writes_no_audit_row(session: AsyncSession) -> None:
    """Only a change that happened is recorded; a rejected form is not an action."""
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        await client.post(
            f"/admin/users/{target.id}/date-of-birth",
            data={"date_of_birth": "not-a-date"},
            follow_redirects=False,
        )

    assert await _audit_rows(session) == []


@pytest.mark.asyncio
async def test_changing_personal_info_is_audited(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/personal-info",
            data={"new_name": "Renamed Target", "date_of_birth": "2011-03-02"},
            follow_redirects=False,
        )

    await session.refresh(target)
    assert response.status_code == 303
    assert target.nome == "Renamed Target"
    actions = [row.get("action") for row in await _audit_rows(session)]
    assert "change_personal_info" in actions
