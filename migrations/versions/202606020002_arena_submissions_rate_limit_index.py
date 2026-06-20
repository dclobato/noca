#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Add composite index on arena_submissions(user_id, created_at) for rate-limit window queries.

Revision ID: 202606020002
Revises: 202606020001
Create Date: 2026-06-02 00:02:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "202606020002"
down_revision: str | Sequence[str] | None = "202606020001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add composite index for per-user rolling-window submission count queries."""
    op.create_index(
        "ix_arena_submissions_user_created_at",
        "arena_submissions",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    """Drop the composite index."""
    op.drop_index("ix_arena_submissions_user_created_at", table_name="arena_submissions")
