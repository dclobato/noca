#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add optional Markdown editorials to Contest and Arena problems.

Revision ID: 202608170001
Revises: 202608150001
Create Date: 2026-08-17 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608170001"
down_revision: str | None = "202608150001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable editorial columns to both problem domains."""
    op.add_column("problems", sa.Column("editorial", sa.Text(), nullable=True))
    op.add_column("arena_problems", sa.Column("editorial", sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove the editorial columns from both problem domains."""
    op.drop_column("arena_problems", "editorial")
    op.drop_column("problems", "editorial")
