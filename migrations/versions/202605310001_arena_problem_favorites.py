#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Arena problem favorites table

Revision ID: 202605310001
Revises: 202605280001
Create Date: 2026-05-31 00:01:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202605310001"
down_revision: Union[str, Sequence[str], None] = "202605280001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "arena_problem_favorites",
        sa.Column(
            "problem_id",
            sa.String(36),
            sa.ForeignKey("arena_problems.id", ondelete="CASCADE"),
            primary_key=True,
            comment="FK to arena_problems.",
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("arena_users.id", ondelete="CASCADE"),
            primary_key=True,
            comment="FK to arena_users.",
        ),
    )
    op.create_index(
        "ix_arena_problem_favorites_user_id",
        "arena_problem_favorites",
        ["user_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_arena_problem_favorites_user_id", table_name="arena_problem_favorites")
    op.drop_table("arena_problem_favorites")
