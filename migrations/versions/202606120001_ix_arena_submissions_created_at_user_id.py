#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Add index ix_arena_submissions_created_at_user_id

The existing (user_id, created_at) index cannot support queries that filter
only by created_at (e.g. the heatmap bulk aggregation).  A (created_at, user_id)
index covers that access pattern efficiently.

Revision ID: 202606120001
Revises: 202606110003
Create Date: 2026-06-12 00:01:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606120001"
down_revision: str | Sequence[str] | None = "202606110003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_arena_submissions_created_at_user_id",
        "arena_submissions",
        ["created_at", "user_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_arena_submissions_created_at_user_id", table_name="arena_submissions")
