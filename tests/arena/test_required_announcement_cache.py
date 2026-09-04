#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The process-local "does any required announcement exist" cache behind the pop-up."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from arena.services import required_announcement_cache as cache_module
from arena.services.required_announcement_cache import (
    REQUIRED_ANNOUNCEMENTS_CACHE_SECONDS,
    invalidate_required_announcements_cache,
    required_announcements_exist,
    required_announcements_known_absent,
)
from shared.enumerations import AnnouncementDomain
from shared.services.announcement_service import create_announcement


async def _publish(session: AsyncSession, *, required: bool = True) -> None:
    await create_announcement(
        session,
        domain=AnnouncementDomain.ARENA,
        title="Read me",
        body="Body.",
        required=required,
        published_by_id="admin-1",
        published_by_label="root",
    )
    await session.commit()


@pytest.mark.asyncio
async def test_unknown_is_not_absent_until_the_database_says_so(session: AsyncSession) -> None:
    assert required_announcements_known_absent() is False

    assert await required_announcements_exist(session) is False

    assert required_announcements_known_absent() is True


@pytest.mark.asyncio
async def test_cached_answer_outlives_a_direct_publish_until_invalidated(session: AsyncSession) -> None:
    assert await required_announcements_exist(session) is False
    await _publish(session)

    assert await required_announcements_exist(session) is False
    assert required_announcements_known_absent() is True

    invalidate_required_announcements_cache()

    assert required_announcements_known_absent() is False
    assert await required_announcements_exist(session) is True
    assert required_announcements_known_absent() is False


@pytest.mark.asyncio
async def test_cached_answer_expires_after_the_ttl(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1_000.0
    monkeypatch.setattr(cache_module, "_clock", lambda: now)
    assert await required_announcements_exist(session) is False
    await _publish(session)

    now += REQUIRED_ANNOUNCEMENTS_CACHE_SECONDS - 1
    assert required_announcements_known_absent() is True

    now += 1
    assert required_announcements_known_absent() is False
    assert await required_announcements_exist(session) is True


@pytest.mark.asyncio
async def test_a_non_required_announcement_leaves_the_answer_absent(session: AsyncSession) -> None:
    await _publish(session, required=False)

    assert await required_announcements_exist(session) is False
    assert required_announcements_known_absent() is True
