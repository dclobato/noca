#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Authenticated Arena notification JSON endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from arena.services.user_timezone_service import format_user_datetime
from shared.enumerations import ARENA_NOTIFICATION_ICONS, ArenaNotificationKind
from shared.services.arena_notification_service import (
    DEFAULT_NOTIFICATION_LIMIT,
    count_unread_arena_notifications,
    list_latest_arena_notifications,
    mark_all_arena_notifications_read,
    mark_arena_notification_read,
)

router = APIRouter(prefix="/arena/notifications", tags=["arena-notifications"])

CurrentArenaUser = Annotated[ArenaUser | None, Depends(get_current_arena_user)]
DatabaseSession = Annotated[AsyncSession, Depends(get_db)]


def _require_user(current_user: ArenaUser | None) -> ArenaUser:
    """Return an authenticated user or raise 401."""
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return current_user


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


_ICON_FALLBACK = "notifications"


def _serialize_notification(row: RowMapping, user: ArenaUser) -> dict[str, Any]:
    kind = row["notification_kind"]
    try:
        icon = ARENA_NOTIFICATION_ICONS.get(ArenaNotificationKind(kind), _ICON_FALLBACK)
    except ValueError:
        icon = _ICON_FALLBACK
    return {
        "id": row["id"],
        "notification_kind": kind,
        "icon": icon,
        "title": row["title"],
        "message": row["message"],
        "target_url": row["target_url"],
        "source_ref": row["source_ref"],
        "context": row["context"],
        "read_at": _isoformat(row["read_at"]),
        "read_at_display": format_user_datetime(row["read_at"], user, fallback=""),
        "created_at": _isoformat(row["created_at"]),
        "created_at_display": format_user_datetime(row["created_at"], user, fallback=""),
    }


@router.get("", name="arena_notifications_list")
async def arena_notifications_list(
    current_user: CurrentArenaUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Return the latest notifications for the current Arena user."""
    user = _require_user(current_user)
    rows = await list_latest_arena_notifications(
        session,
        user_id=user.id,
        limit=DEFAULT_NOTIFICATION_LIMIT,
    )
    unread_count = await count_unread_arena_notifications(session, user_id=user.id)
    return {
        "notifications": [_serialize_notification(row, user) for row in rows],
        "unread_count": unread_count,
        "limit": DEFAULT_NOTIFICATION_LIMIT,
    }


@router.post("/read-all", name="arena_notifications_mark_all_read")
async def arena_notifications_mark_all_read(
    current_user: CurrentArenaUser,
    session: DatabaseSession,
) -> dict[str, int | bool]:
    """Mark all unread notifications as read for the current Arena user."""
    user = _require_user(current_user)
    await mark_all_arena_notifications_read(session, user_id=user.id)
    await session.commit()
    unread_count = await count_unread_arena_notifications(session, user_id=user.id)
    return {"ok": True, "unread_count": unread_count}


@router.post("/{notification_id}/read", name="arena_notification_mark_read")
async def arena_notification_mark_read(
    notification_id: str,
    current_user: CurrentArenaUser,
    session: DatabaseSession,
) -> dict[str, int | bool]:
    """Mark one notification as read for the current Arena user."""
    user = _require_user(current_user)
    found = await mark_arena_notification_read(
        session,
        notification_id=notification_id,
        user_id=user.id,
    )
    if not found:
        raise HTTPException(status_code=404, detail="Notification not found")
    await session.commit()
    unread_count = await count_unread_arena_notifications(session, user_id=user.id)
    return {"ok": True, "unread_count": unread_count}
