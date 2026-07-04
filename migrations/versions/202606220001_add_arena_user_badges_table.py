#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add arena_user_badges table.

Creates the gamification badge ledger associating Arena users with the badges
they have earned and when. One row per user+badge, with a unique constraint so a
badge is awarded to a user at most once.

Revision ID: 202606220001
Revises: 202606200001
Create Date: 2026-06-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202606220001"
down_revision: str | Sequence[str] | None = "202606200001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BADGE_VALUES = (
    "helloworld",
    "oneshot",
    "fullclear",
    "bugkiller",
    "cleancode",
    "bitscrubber",
    "nightworker",
    "weekendworker",
    "strike3",
    "strike7",
    "strike30",
    "nevergiveup",
)


def _badge_enum() -> postgresql.ENUM:
    return postgresql.ENUM(*_BADGE_VALUES, name="arenabadge", create_type=False)


def upgrade() -> None:
    """Create the arena_user_badges table and its badge enum type."""
    bind = op.get_bind()
    postgresql.ENUM(*_BADGE_VALUES, name="arenabadge").create(bind, checkfirst=True)
    op.create_table(
        "arena_user_badges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=36),
            nullable=False,
            comment="FK to arena_users. The user who earned this badge.",
        ),
        sa.Column(
            "badge",
            _badge_enum(),
            nullable=False,
            comment="Badge identifier; equals the basename of the badge image asset.",
        ),
        sa.Column(
            "awarded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="Timestamp when the badge was awarded to the user.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["arena_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "badge", name="uq_arena_user_badges_user_badge"),
    )
    op.create_index("ix_arena_user_badges_user_id", "arena_user_badges", ["user_id"])


def downgrade() -> None:
    """Drop the arena_user_badges table and its badge enum type."""
    op.drop_index("ix_arena_user_badges_user_id", table_name="arena_user_badges")
    op.drop_table("arena_user_badges")
    postgresql.ENUM(*_BADGE_VALUES, name="arenabadge").drop(op.get_bind(), checkfirst=True)
