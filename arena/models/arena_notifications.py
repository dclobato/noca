#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ORM model for durable Arena user notifications."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Mapped, relationship

from arena.database import ArenaBase
from shared.db_schema.arena.arena_notifications import arena_notifications as arena_notifications_table

if TYPE_CHECKING:
    from arena.models.arena_users import ArenaUser


class ArenaNotification(ArenaBase):
    """A durable notification addressed to one Arena user.

    Attributes:
        id: UUID string primary key.
        user_id: Recipient Arena user id.
        notification_kind: Producer event kind.
        title: Short display title.
        message: Human-readable body text.
        target_url: Optional destination URL; nullable until a concrete page exists.
        source_ref: Optional producer-owned idempotency reference.
        context: Optional small JSON payload for future clients.
        read_at: Timestamp when the user read the notification.
        created_at: Record creation timestamp.
        user: Back-reference to the recipient user.
    """

    __table__ = arena_notifications_table

    id: Mapped[str]
    user_id: Mapped[str]
    notification_kind: Mapped[str]
    title: Mapped[str]
    message: Mapped[str]
    target_url: Mapped[str | None]
    source_ref: Mapped[str | None]
    context: Mapped[dict[str, Any] | None]
    read_at: Mapped[datetime | None]
    created_at: Mapped[datetime]

    user: Mapped[ArenaUser] = relationship(
        "ArenaUser",
        back_populates="notifications",
        foreign_keys=[arena_notifications_table.c.user_id],
    )
