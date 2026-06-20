#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Add note column to arena_problems

Revision ID: 202606010002
Revises: 202606010001
Create Date: 2026-06-01 00:02:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606010002"
down_revision: Union[str, Sequence[str], None] = "202606010001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "arena_problems",
        sa.Column(
            "notes",
            sa.String(256),
            nullable=True,
            comment="Internal management note, not shown to regular users.",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("arena_problems", "notes")
