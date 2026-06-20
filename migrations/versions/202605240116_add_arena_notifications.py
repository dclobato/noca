#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add durable Arena notifications.

Revision ID: 202605240116
Revises: 202605240115
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605240116"
down_revision: str | Sequence[str] | None = "202605240115"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the Arena notifications table."""
    op.create_table(
        "arena_notifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "notification_kind",
            sa.String(length=64),
            nullable=False,
            comment="ArenaNotificationKind value identifying the producer event.",
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("target_url", sa.String(length=500), nullable=True),
        sa.Column(
            "source_ref",
            sa.String(length=128),
            nullable=True,
            comment="Producer-owned idempotency reference, such as an Arena judgment id.",
        ),
        sa.Column(
            "context",
            sa.JSON(),
            nullable=True,
            comment="Optional small JSON payload for future URL building or display metadata.",
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["arena_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "notification_kind",
            "source_ref",
            name="uq_arena_notifications_user_kind_source",
        ),
    )
    op.create_index(op.f("ix_arena_notifications_user_id"), "arena_notifications", ["user_id"], unique=False)
    op.create_index(
        "ix_arena_notifications_user_read_created",
        "arena_notifications",
        ["user_id", "read_at", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the Arena notifications table."""
    op.drop_index("ix_arena_notifications_user_read_created", table_name="arena_notifications")
    op.drop_index(op.f("ix_arena_notifications_user_id"), table_name="arena_notifications")
    op.drop_table("arena_notifications")
