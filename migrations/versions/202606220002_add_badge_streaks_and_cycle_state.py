#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add badge streak columns and the badge cycle-state table.

Adds the per-user solve-streak bookkeeping (``current_streak``, ``longest_streak``,
``last_ac_date``) consumed by the rating worker's badge-assignment loop, and the
singleton ``arena_badge_cycle_state`` table holding the loop's incremental
watermark and last full-reconciliation timestamp.

Revision ID: 202606220002
Revises: 202606220001
Create Date: 2026-06-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202606220002"
down_revision: str | Sequence[str] | None = "202606220001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add streak columns to arena_users and create arena_badge_cycle_state."""
    op.add_column(
        "arena_users",
        sa.Column(
            "current_streak",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Consecutive solve-day streak ending at last_ac_date; recomputed by the rating badge loop",
        ),
    )
    op.add_column(
        "arena_users",
        sa.Column(
            "longest_streak",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Longest consecutive solve-day run ever achieved; basis for STRIKE badges",
        ),
    )
    op.add_column(
        "arena_users",
        sa.Column(
            "last_ac_date",
            sa.Date(),
            nullable=True,
            comment="Local-timezone date of the user's most recent accepted submission (badge streaks)",
        ),
    )
    op.create_table(
        "arena_badge_cycle_state",
        sa.Column("id", sa.String(length=16), nullable=False, comment="Fixed singleton key; always 'singleton'."),
        sa.Column(
            "last_processed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="High-watermark: max judgment finished_at processed by the incremental pass.",
        ),
        sa.Column(
            "last_reconciled_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp of the last full reconciliation pass over all AC history.",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 'singleton'", name="ck_arena_badge_cycle_state_singleton"),
    )


def downgrade() -> None:
    """Drop arena_badge_cycle_state and the streak columns."""
    op.drop_table("arena_badge_cycle_state")
    op.drop_column("arena_users", "last_ac_date")
    op.drop_column("arena_users", "longest_streak")
    op.drop_column("arena_users", "current_streak")
