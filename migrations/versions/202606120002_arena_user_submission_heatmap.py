#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Add arena_user_submission_heatmap table

Stores a precomputed submission-per-day heatmap (last 52 weeks) for each
Arena user so the profile page can render the calendar chart without running
aggregate queries on every request.

Revision ID: 202606120002
Revises: 202606120001
Create Date: 2026-06-12 00:02:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606120002"
down_revision: str | Sequence[str] | None = "202606120001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "arena_user_submission_heatmap",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("arena_users.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("range_start", sa.String(10), nullable=False),
        sa.Column("range_end", sa.String(10), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("arena_user_submission_heatmap")
