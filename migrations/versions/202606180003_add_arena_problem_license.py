#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add optional license information to Arena problems.

Revision ID: 202606180003
Revises: 202606180002
Create Date: 2026-06-18 00:03:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202606180003"
down_revision: str | None = "202606180002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable problem license column."""
    op.add_column(
        "arena_problems",
        sa.Column("license", sa.String(length=256), nullable=True),
    )


def downgrade() -> None:
    """Drop the problem license column."""
    op.drop_column("arena_problems", "license")
