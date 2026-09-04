#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The shared announcement board service: validation, domain scoping, pagination, immutability."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import AnnouncementDomain
from shared.services import announcement_service
from shared.services.announcement_service import (
    PAGE_SIZE,
    AnnouncementRow,
    create_announcement,
    delete_announcement,
    get_announcement,
    list_announcements,
)


async def _publish(
    session: AsyncSession,
    *,
    domain: AnnouncementDomain = AnnouncementDomain.WEB,
    title: str = "Hello",
    body: str = "Some **news**.",
    required: bool = False,
) -> AnnouncementRow:
    return await create_announcement(
        session,
        domain=domain,
        title=title,
        body=body,
        required=required,
        published_by_id="admin-1",
        published_by_label="root",
    )


@pytest.mark.asyncio
async def test_create_strips_fields_and_stores_the_publisher_snapshot(session: AsyncSession) -> None:
    row = await _publish(session, title="  Spaced title  ", body="\n\nBody text.\n")

    stored = await get_announcement(session, domain=AnnouncementDomain.WEB, announcement_id=row.id)
    assert stored is not None
    assert stored.title == "Spaced title"
    assert stored.body == "Body text."
    assert stored.required is False
    assert stored.published_by_id == "admin-1"
    assert stored.published_by_label == "root"
    assert stored.published_at is not None


@pytest.mark.asyncio
async def test_create_persists_a_required_flag_for_the_arena_surface(session: AsyncSession) -> None:
    """The shared contract carries ``required`` so the Arena slice can set it; Web passes False."""
    row = await _publish(session, domain=AnnouncementDomain.ARENA, required=True)

    stored = await get_announcement(session, domain=AnnouncementDomain.ARENA, announcement_id=row.id)
    assert stored is not None
    assert stored.required is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("title", "body", "fragment"),
    [
        ("   ", "Body", "Title is required."),
        ("x" * 257, "Body", "longer than 256"),
        ("Title", "   ", "Body is required."),
        ("Title", "Hello <script>alert(1)</script>", "disallowed content: html"),
        ("Title", "![shot](https://example.com/a.png)", "disallowed content: image"),
    ],
)
async def test_create_refuses_invalid_input(session: AsyncSession, title: str, body: str, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        await _publish(session, title=title, body=body)


@pytest.mark.asyncio
async def test_create_accepts_external_links_latex_and_mermaid(session: AsyncSession) -> None:
    """An announcement is a statement-grade body whose one extra freedom is linking out."""
    body = "See [the docs](https://example.com/docs) and $x^2$.\n\n```mermaid\ngraph TD\n  A-->B\n```"

    row = await _publish(session, body=body)

    stored = await get_announcement(session, domain=AnnouncementDomain.WEB, announcement_id=row.id)
    assert stored is not None
    assert "https://example.com/docs" in stored.body


@pytest.mark.asyncio
async def test_list_pages_by_twenty_five_newest_first_and_clamps(session: AsyncSession) -> None:
    rows = [await _publish(session, title=f"Announcement {index:02d}") for index in range(PAGE_SIZE + 1)]

    first = await list_announcements(session, domain=AnnouncementDomain.WEB, page=1)
    second = await list_announcements(session, domain=AnnouncementDomain.WEB, page=2)
    clamped = await list_announcements(session, domain=AnnouncementDomain.WEB, page=99)

    assert first.total == PAGE_SIZE + 1
    assert first.per_page == PAGE_SIZE
    assert len(first.items) == PAGE_SIZE
    assert first.items[0].id == rows[-1].id
    assert len(second.items) == 1
    assert second.items[0].id == rows[0].id
    assert clamped.page == 2
    assert [item.id for item in clamped.items] == [rows[0].id]


@pytest.mark.asyncio
async def test_reads_and_deletes_are_scoped_to_the_domain(session: AsyncSession) -> None:
    web_row = await _publish(session, domain=AnnouncementDomain.WEB, title="Web only")
    arena_row = await _publish(session, domain=AnnouncementDomain.ARENA, title="Arena only")

    web_list = await list_announcements(session, domain=AnnouncementDomain.WEB, page=1)
    assert [item.id for item in web_list.items] == [web_row.id]
    assert await get_announcement(session, domain=AnnouncementDomain.WEB, announcement_id=arena_row.id) is None

    assert await delete_announcement(session, domain=AnnouncementDomain.WEB, announcement_id=arena_row.id) is False
    assert await get_announcement(session, domain=AnnouncementDomain.ARENA, announcement_id=arena_row.id) is not None

    assert await delete_announcement(session, domain=AnnouncementDomain.WEB, announcement_id=web_row.id) is True
    assert await get_announcement(session, domain=AnnouncementDomain.WEB, announcement_id=web_row.id) is None
    assert await delete_announcement(session, domain=AnnouncementDomain.WEB, announcement_id=web_row.id) is False


def test_the_service_offers_no_update_path() -> None:
    """Published announcements are immutable: the module exposes create, read, and delete only."""
    public_callables = {name for name in dir(announcement_service) if not name.startswith("_")}

    assert not {name for name in public_callables if "update" in name or "edit" in name}
