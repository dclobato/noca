#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Add description column to arena_problem_sets

Revision ID: 202606040005
Revises: 202606040004
Create Date: 2026-06-04 00:05:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606040005"
down_revision: Union[str, Sequence[str], None] = "202606040004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "arena_problem_sets",
        sa.Column(
            "description",
            sa.Text(),
            nullable=True,
            comment="Teacher-facing notes/description for the problem set.",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("arena_problem_sets", "description")
