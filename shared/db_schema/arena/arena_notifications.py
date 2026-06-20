#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Core table definition for durable Arena user notifications."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from .._base import _created_at_column, _id_column, metadata

arena_notifications = Table(
    "arena_notifications",
    metadata,
    _id_column(),
    Column(
        "user_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "notification_kind",
        String(64),
        nullable=False,
        comment="ArenaNotificationKind value identifying the producer event.",
    ),
    Column("title", String(200), nullable=False),
    Column("message", Text, nullable=False),
    Column("target_url", String(500), nullable=True),
    Column(
        "source_ref",
        String(128),
        nullable=True,
        comment="Producer-owned idempotency reference, such as an Arena judgment id.",
    ),
    Column(
        "context",
        JSON,
        nullable=True,
        comment="Optional small JSON payload for future URL building or display metadata.",
    ),
    Column("read_at", DateTime(timezone=True), nullable=True),
    _created_at_column(),
    UniqueConstraint(
        "user_id",
        "notification_kind",
        "source_ref",
        name="uq_arena_notifications_user_kind_source",
    ),
    Index(
        "ix_arena_notifications_user_read_created",
        "user_id",
        "read_at",
        "created_at",
    ),
)
