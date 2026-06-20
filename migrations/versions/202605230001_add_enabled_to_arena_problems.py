"""Add enabled to arena_problems.

Revision ID: 202605230001
Revises: 364f67aa423f
Create Date: 2026-05-23 00:01:00.000000

"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605230001"
down_revision: str | Sequence[str] | None = "364f67aa423f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the enabled flag to arena problems."""
    op.add_column(
        "arena_problems",
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment="Whether the problem is available for arena use.",
        ),
    )


def downgrade() -> None:
    """Remove the enabled flag from arena problems."""
    op.drop_column("arena_problems", "enabled")
