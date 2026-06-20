#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Add composite index on submissions(team_id, created_at) for rate-limit window queries.

Revision ID: 202606020003
Revises: 202606020002
Create Date: 2026-06-02 00:03:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "202606020003"
down_revision: str | Sequence[str] | None = "202606020002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add composite index for per-team rolling-window submission count queries."""
    op.create_index(
        "ix_submissions_team_created_at",
        "submissions",
        ["team_id", "created_at"],
    )


def downgrade() -> None:
    """Drop the composite index."""
    op.drop_index("ix_submissions_team_created_at", table_name="submissions")
