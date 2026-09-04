#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The acknowledgment ledger service: what is pending for whom, and the idempotent acknowledge."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import arena_announcement_acknowledgments
from shared.enumerations import AnnouncementDomain
from shared.services.announcement_acknowledgment_service import (
    acknowledge_announcement,
    has_required_announcements,
    pending_required_announcement,
)
from shared.services.announcement_service import AnnouncementRow, create_announcement


async def _publish(
    session: AsyncSession,
    *,
    title: str = "Read me",
    domain: AnnouncementDomain = AnnouncementDomain.ARENA,
    required: bool = True,
) -> AnnouncementRow:
    return await create_announcement(
        session,
        domain=domain,
        title=title,
        body="Body.",
        required=required,
        published_by_id="admin-1",
        published_by_label="root",
    )


async def _ledger_size(session: AsyncSession) -> int:
    return int(
        (await session.execute(select(func.count()).select_from(arena_announcement_acknowledgments))).scalar_one()
    )


@pytest.mark.asyncio
async def test_nothing_pending_without_required_arena_announcements(session: AsyncSession) -> None:
    await _publish(session, required=False)
    await _publish(session, domain=AnnouncementDomain.WEB, required=True)

    assert await pending_required_announcement(session, user_id="u1") is None


@pytest.mark.asyncio
async def test_has_required_announcements_counts_only_required_arena_rows(session: AsyncSession) -> None:
    assert await has_required_announcements(session) is False
    await _publish(session, required=False)
    await _publish(session, domain=AnnouncementDomain.WEB, required=True)
    assert await has_required_announcements(session) is False

    await _publish(session, required=True)

    assert await has_required_announcements(session) is True


@pytest.mark.asyncio
async def test_oldest_required_announcement_is_pending_with_the_queue_size(session: AsyncSession) -> None:
    first = await _publish(session, title="First")
    await _publish(session, title="Second")
    await _publish(session, title="Third")

    pending = await pending_required_announcement(session, user_id="u1")

    assert pending is not None
    assert pending.announcement.id == first.id
    assert pending.pending_total == 3


@pytest.mark.asyncio
async def test_acknowledging_advances_the_queue_for_that_user_only(session: AsyncSession) -> None:
    first = await _publish(session, title="First")
    second = await _publish(session, title="Second")

    assert await acknowledge_announcement(session, user_id="u1", announcement_id=first.id) is True

    next_for_u1 = await pending_required_announcement(session, user_id="u1")
    still_for_u2 = await pending_required_announcement(session, user_id="u2")
    assert next_for_u1 is not None and next_for_u1.announcement.id == second.id
    assert next_for_u1.pending_total == 1
    assert still_for_u2 is not None and still_for_u2.announcement.id == first.id
    assert still_for_u2.pending_total == 2

    assert await acknowledge_announcement(session, user_id="u1", announcement_id=second.id) is True
    assert await pending_required_announcement(session, user_id="u1") is None


@pytest.mark.asyncio
async def test_acknowledge_is_idempotent(session: AsyncSession) -> None:
    row = await _publish(session)

    assert await acknowledge_announcement(session, user_id="u1", announcement_id=row.id) is True
    assert await acknowledge_announcement(session, user_id="u1", announcement_id=row.id) is True

    assert await _ledger_size(session) == 1


@pytest.mark.asyncio
async def test_acknowledge_refuses_what_is_not_a_required_arena_announcement(session: AsyncSession) -> None:
    optional = await _publish(session, required=False)
    web = await _publish(session, domain=AnnouncementDomain.WEB, required=True)

    assert await acknowledge_announcement(session, user_id="u1", announcement_id=optional.id) is False
    assert await acknowledge_announcement(session, user_id="u1", announcement_id=web.id) is False
    assert await acknowledge_announcement(session, user_id="u1", announcement_id="missing") is False
    assert await _ledger_size(session) == 0
