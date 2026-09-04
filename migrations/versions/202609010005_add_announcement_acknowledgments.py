"""Record which Arena user acknowledged which required announcement.

Revision ID: 202609010005
Revises: 202609010004
Create Date: 2026-09-01
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202609010005"
down_revision: str | Sequence[str] | None = "202609010004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the append-only acknowledgment ledger; no backfill, existing users must acknowledge."""
    op.create_table(
        "arena_announcement_acknowledgments",
        sa.Column(
            "announcement_id",
            sa.String(length=36),
            sa.ForeignKey("announcements.id", ondelete="CASCADE"),
            primary_key=True,
            comment="FK to the required announcement that was acknowledged.",
        ),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("arena_users.id", ondelete="CASCADE"),
            primary_key=True,
            comment="FK to the Arena user who acknowledged it.",
        ),
        sa.Column(
            "acknowledged_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="Time when the user ticked the acknowledgment box.",
        ),
    )
    op.create_index(
        "ix_arena_announcement_acknowledgments_user_id",
        "arena_announcement_acknowledgments",
        ["user_id"],
    )


def downgrade() -> None:
    """Drop the acknowledgment ledger."""
    op.drop_index(
        "ix_arena_announcement_acknowledgments_user_id",
        table_name="arena_announcement_acknowledgments",
    )
    op.drop_table("arena_announcement_acknowledgments")
