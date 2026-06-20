#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared helpers for durable Arena notifications.

All helpers leave transaction ownership to the caller. They never call
``commit()`` or ``rollback()`` so producers can group notification changes with
their own domain updates.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import RowMapping

from shared.db_schema.arena.arena_notifications import arena_notifications
from shared.enumerations import ArenaNotificationKind

DEFAULT_NOTIFICATION_LIMIT = 20


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _dialect_name(executor: Any) -> str:
    dialect = getattr(executor, "dialect", None)
    if dialect is not None:
        return cast(str, dialect.name)
    bind = executor.get_bind()
    return cast(str, bind.dialect.name)


def _insert_statement(executor: Any, values: dict[str, Any]) -> Any:
    dialect_name = _dialect_name(executor)
    if dialect_name == "postgresql":
        return (
            postgresql_insert(arena_notifications)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[
                    arena_notifications.c.user_id,
                    arena_notifications.c.notification_kind,
                    arena_notifications.c.source_ref,
                ]
            )
        )
    if dialect_name == "sqlite":
        return (
            sqlite_insert(arena_notifications)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[
                    arena_notifications.c.user_id,
                    arena_notifications.c.notification_kind,
                    arena_notifications.c.source_ref,
                ]
            )
        )
    return arena_notifications.insert().values(**values)


async def create_arena_notification(
    executor: Any,
    *,
    user_id: str,
    notification_kind: ArenaNotificationKind,
    title: str,
    message: str,
    target_url: str | None = None,
    source_ref: str | None = None,
    context: Mapping[str, Any] | None = None,
) -> str:
    """Insert one Arena notification without committing.

    Args:
        executor: Async SQLAlchemy session or connection.
        user_id: Recipient Arena user id.
        notification_kind: Notification category.
        title: Short display title.
        message: Human-readable body text.
        target_url: Optional destination URL. Use ``None`` until a real target exists.
        source_ref: Optional producer-owned idempotency key.
        context: Optional small JSON payload for future clients.

    Returns:
        str: The generated notification id. If an idempotent insert is ignored,
        this is still the id that was attempted, not the existing row id.

    Notes:
        The caller owns the transaction and must commit or roll back.
    """
    notification_id = str(uuid.uuid4())
    values = {
        "id": notification_id,
        "user_id": user_id,
        "notification_kind": notification_kind.value,
        "title": title,
        "message": message,
        "target_url": target_url,
        "source_ref": source_ref,
        "context": dict(context) if context is not None else None,
        "created_at": _utcnow(),
    }
    await executor.execute(_insert_statement(executor, values))
    return notification_id


async def count_unread_arena_notifications(executor: Any, *, user_id: str) -> int:
    """Return the number of unread notifications for one Arena user."""
    count = await executor.execute(
        select(func.count())
        .select_from(arena_notifications)
        .where(
            arena_notifications.c.user_id == user_id,
            arena_notifications.c.read_at.is_(None),
        )
    )
    return int(count.scalar_one() or 0)


def _latest_notifications_statement(user_id: str, limit: int) -> Select[tuple[Any, ...]]:
    return (
        select(arena_notifications)
        .where(arena_notifications.c.user_id == user_id)
        .order_by(arena_notifications.c.created_at.desc(), arena_notifications.c.id.desc())
        .limit(limit)
    )


async def list_latest_arena_notifications(
    executor: Any,
    *,
    user_id: str,
    limit: int = DEFAULT_NOTIFICATION_LIMIT,
) -> list[RowMapping]:
    """Return latest notifications for one Arena user, newest first."""
    effective_limit = max(1, min(limit, DEFAULT_NOTIFICATION_LIMIT))
    result = await executor.execute(_latest_notifications_statement(user_id, effective_limit))
    return list(result.mappings().all())


async def mark_arena_notification_read(
    executor: Any,
    *,
    notification_id: str,
    user_id: str,
) -> bool:
    """Mark one user-owned notification as read without committing.

    Returns:
        bool: ``True`` when a matching notification exists, otherwise ``False``.
    """
    result = await executor.execute(
        update(arena_notifications)
        .where(
            arena_notifications.c.id == notification_id,
            arena_notifications.c.user_id == user_id,
        )
        .values(read_at=func.coalesce(arena_notifications.c.read_at, _utcnow()))
    )
    return bool(result.rowcount)


async def paginate_arena_notifications(
    executor: Any,
    *,
    user_id: str,
    page: int = 1,
    per_page: int = 25,
) -> tuple[list[RowMapping], int]:
    """Return a paginated slice of notifications for one Arena user, newest first.

    Args:
        executor: Async SQLAlchemy session or connection.
        user_id: Arena user id.
        page: Requested page number (one-based, clamped to valid range).
        per_page: Page size (clamped to at least one).

    Returns:
        tuple[list[RowMapping], int]: The notification rows for the requested
        page and the total notification count for the user.

    Notes:
        The caller owns the transaction and must not rely on this function to
        commit or roll back.
    """
    effective_per_page = max(1, per_page)
    count_result = await executor.execute(
        select(func.count()).select_from(arena_notifications).where(arena_notifications.c.user_id == user_id)
    )
    total = int(count_result.scalar_one() or 0)

    if total > 0:
        pages = (total + effective_per_page - 1) // effective_per_page
        effective_page = min(max(1, page), pages)
    else:
        effective_page = 1

    offset = (effective_page - 1) * effective_per_page
    rows_result = await executor.execute(
        select(arena_notifications)
        .where(arena_notifications.c.user_id == user_id)
        .order_by(arena_notifications.c.created_at.desc(), arena_notifications.c.id.desc())
        .limit(effective_per_page)
        .offset(offset)
    )
    return list(rows_result.mappings().all()), total


async def delete_arena_notification(
    executor: Any,
    *,
    notification_id: str,
    user_id: str,
) -> bool:
    """Delete one user-owned notification without committing.

    Args:
        executor: Async SQLAlchemy session or connection.
        notification_id: Notification id to delete.
        user_id: Arena user id that must own the notification.

    Returns:
        bool: ``True`` when a matching notification was deleted, otherwise ``False``.

    Notes:
        The caller owns the transaction and must commit or roll back.
    """
    result = await executor.execute(
        delete(arena_notifications).where(
            arena_notifications.c.id == notification_id,
            arena_notifications.c.user_id == user_id,
        )
    )
    return bool(result.rowcount)


async def mark_all_arena_notifications_read(
    executor: Any,
    *,
    user_id: str,
) -> int:
    """Mark all unread notifications for one Arena user as read without committing.

    Args:
        executor: Async SQLAlchemy session or connection.
        user_id: Arena user id whose unread notifications will be marked read.

    Returns:
        int: Number of rows updated.

    Notes:
        The caller owns the transaction and must commit or roll back.
    """
    result = await executor.execute(
        update(arena_notifications)
        .where(
            arena_notifications.c.user_id == user_id,
            arena_notifications.c.read_at.is_(None),
        )
        .values(read_at=_utcnow())
    )
    return int(result.rowcount)


async def delete_all_arena_notifications(
    executor: Any,
    *,
    user_id: str,
) -> int:
    """Delete all notifications for one Arena user without committing.

    Args:
        executor: Async SQLAlchemy session or connection.
        user_id: Arena user id whose notifications will be removed.

    Returns:
        int: Number of deleted rows.

    Notes:
        The caller owns the transaction and must commit or roll back.
    """
    result = await executor.execute(delete(arena_notifications).where(arena_notifications.c.user_id == user_id))
    return int(result.rowcount)
