#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add nullable size columns to arena_test_cases (Migration A).

Phase one of moving Arena test-case content from the database to the shared
filesystem. The two size columns are added nullable and online; the backfill
script (scripts/migrate_arena_tc_to_fs.py) writes files and populates them
before Migration B drops the content columns.

Revision ID: 202606180004
Revises: 202606180003
Create Date: 2026-06-18 00:04:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202606180004"
down_revision: str | None = "202606180003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable input/output size columns."""
    op.add_column(
        "arena_test_cases",
        sa.Column("input_size_bytes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "arena_test_cases",
        sa.Column("output_size_bytes", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Drop the input/output size columns."""
    op.drop_column("arena_test_cases", "output_size_bytes")
    op.drop_column("arena_test_cases", "input_size_bytes")
