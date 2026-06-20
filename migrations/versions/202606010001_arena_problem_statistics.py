#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Arena precomputed problem statistics table

Revision ID: 202606010001
Revises: 202605310001
Create Date: 2026-06-01 00:01:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606010001"
down_revision: Union[str, Sequence[str], None] = "202605310001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "arena_problem_statistics",
        sa.Column(
            "problem_id",
            sa.String(36),
            sa.ForeignKey("arena_problems.id", ondelete="CASCADE"),
            primary_key=True,
            comment="FK to arena_problems.",
        ),
        sa.Column(
            "data",
            sa.JSON(),
            nullable=False,
            comment="Precomputed statistics payload: verdicts, languages, time/memory stats, histogram.",
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("arena_problem_statistics")
