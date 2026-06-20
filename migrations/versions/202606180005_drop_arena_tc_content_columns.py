#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Drop arena_test_cases content columns (Migration B).

Phase two of moving Arena test-case content to the shared filesystem. Run this
only after scripts/migrate_arena_tc_to_fs.py has written every test case to
``<root>/arena/<problem_id>/NNN.in|out`` and verified per-problem file/row
counts. The downgrade re-adds the (empty) content columns for rollback safety;
content itself is not restored from disk.

Revision ID: 202606180005
Revises: 202606180004
Create Date: 2026-06-18 00:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202606180005"
down_revision: str | None = "202606180004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the input/output content columns now that content lives on disk."""
    op.drop_column("arena_test_cases", "input_content")
    op.drop_column("arena_test_cases", "output_content")


def downgrade() -> None:
    """Re-add the nullable content columns (without restoring content)."""
    op.add_column(
        "arena_test_cases",
        sa.Column("output_content", sa.Text(), nullable=True),
    )
    op.add_column(
        "arena_test_cases",
        sa.Column("input_content", sa.Text(), nullable=True),
    )
