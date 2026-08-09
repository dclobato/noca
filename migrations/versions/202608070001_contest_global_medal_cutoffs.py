"""Add contest-level (global) medal cutoffs.

Revision ID: 202608070001
Revises: 202608030002
Create Date: 2026-08-07 00:01:00.000000

"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608070001"
down_revision: str | Sequence[str] | None = "202608030002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# All-or-nothing invariant. The configured branch asserts IS NOT NULL explicitly
# because a CHECK rejects only FALSE: an expression relying on the comparisons
# alone evaluates to UNKNOWN for a partial triple such as (1, 2, NULL) and would
# let it through.
_CUTOFF_CHECK = (
    "(global_gold_cutoff IS NULL AND global_silver_cutoff IS NULL AND global_bronze_cutoff IS NULL)"
    " OR ("
    "global_gold_cutoff IS NOT NULL"
    " AND global_silver_cutoff IS NOT NULL"
    " AND global_bronze_cutoff IS NOT NULL"
    " AND global_gold_cutoff >= 1"
    " AND global_gold_cutoff <= global_silver_cutoff"
    " AND global_silver_cutoff <= global_bronze_cutoff"
    ")"
)


def upgrade() -> None:
    """Upgrade schema."""
    # Batch mode keeps the CHECK constraint addition portable: SQLite cannot add a
    # table constraint through a bare ALTER TABLE, so it recreates the table instead,
    # while PostgreSQL emits direct ALTER TABLE statements.
    #
    # No server default: NULL is the intended initial value for every existing
    # contest, meaning "no global medals" -- exactly today's behavior.
    with op.batch_alter_table("contests") as batch_op:
        batch_op.add_column(
            sa.Column(
                "global_gold_cutoff",
                sa.Integer(),
                nullable=True,
                comment="Maximum ranking position (inclusive) awarded a gold medal in the global scope.",
            )
        )
        batch_op.add_column(
            sa.Column(
                "global_silver_cutoff",
                sa.Integer(),
                nullable=True,
                comment="Maximum ranking position (inclusive) awarded a silver medal in the global scope.",
            )
        )
        batch_op.add_column(
            sa.Column(
                "global_bronze_cutoff",
                sa.Integer(),
                nullable=True,
                comment="Maximum ranking position (inclusive) awarded a bronze medal in the global scope.",
            )
        )
        batch_op.create_check_constraint("ck_contests_global_medal_cutoffs", _CUTOFF_CHECK)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("contests") as batch_op:
        batch_op.drop_constraint("ck_contests_global_medal_cutoffs", type_="check")
        batch_op.drop_column("global_bronze_cutoff")
        batch_op.drop_column("global_silver_cutoff")
        batch_op.drop_column("global_gold_cutoff")
