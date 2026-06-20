#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for durable Arena notifications."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import arena.models.arena_notifications  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_notifications import ArenaNotification
from arena.models.arena_users import ArenaUser
from arena.routes.notifications import router as arena_notifications_router
from shared.enumerations import ArenaNotificationKind, ArenaRole
from shared.services.arena_notification_service import (
    count_unread_arena_notifications,
    create_arena_notification,
    list_latest_arena_notifications,
    mark_arena_notification_read,
)


async def _make_user(session: AsyncSession, *, name: str = "Notify User") -> ArenaUser:
    """Create and flush one active Arena user."""
    user = ArenaUser(
        nome=name,
        email_normalizado=f"{uuid.uuid4().hex}@test.example.com",
        dta_nascimento=date(1995, 1, 1),
        role=ArenaRole.ARENA_USER,
    )
    user.password = "Senha@Forte1!"
    session.add(user)
    await session.flush()
    return user


def _build_notifications_app(session: AsyncSession, current_user: ArenaUser | None) -> FastAPI:
    """Build a minimal FastAPI app for notification route tests."""
    app = FastAPI()
    app.include_router(arena_notifications_router)

    async def _override_db():
        yield session

    async def _override_current_user():
        return current_user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_arena_user] = _override_current_user
    return app


@pytest.mark.asyncio
async def test_notification_helper_inserts_lists_counts_and_marks_read(session: AsyncSession) -> None:
    """Shared helper handles the normal notification lifecycle without committing."""
    user = await _make_user(session)
    notification_id = await create_arena_notification(
        session,
        user_id=user.id,
        notification_kind=ArenaNotificationKind.SUBMISSION_JUDGED,
        title="Submission judged",
        message="Your submission was judged.",
        source_ref="judgment-1",
    )

    assert await count_unread_arena_notifications(session, user_id=user.id) == 1
    rows = await list_latest_arena_notifications(session, user_id=user.id)
    assert len(rows) == 1
    assert rows[0]["id"] == notification_id
    assert rows[0]["target_url"] is None

    marked = await mark_arena_notification_read(session, notification_id=notification_id, user_id=user.id)
    assert marked is True
    assert await count_unread_arena_notifications(session, user_id=user.id) == 0


@pytest.mark.asyncio
async def test_notification_helper_does_not_commit(engine, session: AsyncSession) -> None:
    """Notification creation leaves transaction ownership to the caller."""
    user = await _make_user(session)
    await session.commit()

    await create_arena_notification(
        session,
        user_id=user.id,
        notification_kind=ArenaNotificationKind.SUBMISSION_JUDGED,
        title="Submission judged",
        message="Your submission was judged.",
        source_ref="judgment-uncommitted",
    )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as other_session:
        rows = (
            (
                await other_session.execute(
                    select(ArenaNotification).where(ArenaNotification.source_ref == "judgment-uncommitted")
                )
            )
            .scalars()
            .all()
        )
        assert rows == []


@pytest.mark.asyncio
async def test_notification_helper_ignores_duplicate_source_ref(session: AsyncSession) -> None:
    """Idempotent producer references do not create duplicate notifications."""
    user = await _make_user(session)
    for _ in range(2):
        await create_arena_notification(
            session,
            user_id=user.id,
            notification_kind=ArenaNotificationKind.SUBMISSION_JUDGED,
            title="Submission judged",
            message="Your submission was judged.",
            source_ref="judgment-duplicate",
        )

    rows = await list_latest_arena_notifications(session, user_id=user.id)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_latest_notifications_are_limited_to_twenty(session: AsyncSession) -> None:
    """List helper returns at most the v1 dropdown limit."""
    user = await _make_user(session)
    for index in range(25):
        await create_arena_notification(
            session,
            user_id=user.id,
            notification_kind=ArenaNotificationKind.SUBMISSION_JUDGED,
            title=f"Submission judged {index}",
            message="Your submission was judged.",
            source_ref=f"judgment-{index}",
        )

    rows = await list_latest_arena_notifications(session, user_id=user.id, limit=100)
    assert len(rows) == 20


@pytest.mark.asyncio
async def test_notification_rows_cascade_with_user(session: AsyncSession) -> None:
    """Deleting an Arena user deletes their notifications."""
    user = await _make_user(session)
    await create_arena_notification(
        session,
        user_id=user.id,
        notification_kind=ArenaNotificationKind.SUBMISSION_JUDGED,
        title="Submission judged",
        message="Your submission was judged.",
        source_ref="judgment-cascade",
    )
    await session.flush()

    await session.delete(user)
    await session.flush()

    rows = (await session.execute(select(ArenaNotification))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_notifications_route_requires_authentication(session: AsyncSession) -> None:
    """Notification JSON endpoints require an authenticated Arena user."""
    app = _build_notifications_app(session, None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/arena/notifications")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_notifications_route_lists_only_current_user_rows(session: AsyncSession) -> None:
    """Users can only see their own notifications."""
    current_user = await _make_user(session)
    other_user = await _make_user(session, name="Other User")
    await create_arena_notification(
        session,
        user_id=current_user.id,
        notification_kind=ArenaNotificationKind.SUBMISSION_JUDGED,
        title="Mine",
        message="Visible",
        source_ref="mine",
    )
    await create_arena_notification(
        session,
        user_id=other_user.id,
        notification_kind=ArenaNotificationKind.SUBMISSION_JUDGED,
        title="Other",
        message="Hidden",
        source_ref="other",
    )
    await session.commit()

    app = _build_notifications_app(session, current_user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/arena/notifications")

    assert response.status_code == 200
    payload = response.json()
    assert payload["unread_count"] == 1
    assert payload["limit"] == 20
    assert [row["title"] for row in payload["notifications"]] == ["Mine"]


@pytest.mark.asyncio
async def test_mark_read_route_rejects_foreign_notification(session: AsyncSession) -> None:
    """Users cannot mark another user's notification as read."""
    current_user = await _make_user(session)
    other_user = await _make_user(session, name="Other User")
    notification_id = await create_arena_notification(
        session,
        user_id=other_user.id,
        notification_kind=ArenaNotificationKind.SUBMISSION_JUDGED,
        title="Other",
        message="Hidden",
        source_ref="other-read",
    )
    await session.commit()

    app = _build_notifications_app(session, current_user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(f"/arena/notifications/{notification_id}/read")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_mark_read_route_updates_unread_count(session: AsyncSession) -> None:
    """Mark-read endpoint updates read_at and reports the new unread count."""
    current_user = await _make_user(session)
    notification_id = await create_arena_notification(
        session,
        user_id=current_user.id,
        notification_kind=ArenaNotificationKind.SUBMISSION_JUDGED,
        title="Mine",
        message="Visible",
        source_ref="mine-read",
    )
    await session.commit()

    app = _build_notifications_app(session, current_user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(f"/arena/notifications/{notification_id}/read")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "unread_count": 0}
    notification = await session.get(ArenaNotification, notification_id)
    assert notification is not None
    assert notification.read_at is not None


@pytest.mark.asyncio
async def test_notifications_route_includes_icon_field(session: AsyncSession) -> None:
    """Each notification in the list response carries an 'icon' field."""
    from shared.enumerations import ARENA_NOTIFICATION_ICONS

    current_user = await _make_user(session)
    await create_arena_notification(
        session,
        user_id=current_user.id,
        notification_kind=ArenaNotificationKind.SUBMISSION_JUDGED,
        title="Judged",
        message="Your submission was judged.",
        source_ref="icon-test",
    )
    await session.commit()

    app = _build_notifications_app(session, current_user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/arena/notifications")

    assert response.status_code == 200
    notifications = response.json()["notifications"]
    assert len(notifications) == 1
    assert "icon" in notifications[0]
    assert notifications[0]["icon"] == ARENA_NOTIFICATION_ICONS[ArenaNotificationKind.SUBMISSION_JUDGED]


@pytest.mark.asyncio
async def test_notifications_route_icon_values_per_kind(session: AsyncSession) -> None:
    """Each notification kind maps to its declared icon; unknown kinds fall back to 'notifications'."""
    from shared.enumerations import ARENA_NOTIFICATION_ICONS

    current_user = await _make_user(session)
    kinds_and_icons = [
        (ArenaNotificationKind.SUBMISSION_JUDGED, ARENA_NOTIFICATION_ICONS[ArenaNotificationKind.SUBMISSION_JUDGED]),
        (ArenaNotificationKind.OTHER, ARENA_NOTIFICATION_ICONS[ArenaNotificationKind.OTHER]),
    ]
    for index, (kind, _expected_icon) in enumerate(kinds_and_icons):
        await create_arena_notification(
            session,
            user_id=current_user.id,
            notification_kind=kind,
            title=f"Notification {index}",
            message="Message.",
            source_ref=f"icon-kind-{index}",
        )
    await session.commit()

    app = _build_notifications_app(session, current_user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/arena/notifications")

    assert response.status_code == 200
    notifications = response.json()["notifications"]
    # Results are newest-first; zip against reversed expected list
    for row, (_kind, expected_icon) in zip(notifications, reversed(kinds_and_icons), strict=False):
        assert row["icon"] == expected_icon
