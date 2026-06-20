#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add exclude_from_ranking to arena_affiliations.

Revision ID: 202606190001
Revises: 202606180005
Create Date: 2026-06-19
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202606190001"
down_revision: str | Sequence[str] | None = "202606180005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add exclude_from_ranking column to arena_affiliations."""
    op.add_column(
        "arena_affiliations",
        sa.Column(
            "exclude_from_ranking",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="When true, excludes the affiliation from the public ranking and rating computation",
        ),
    )


def downgrade() -> None:
    """Remove exclude_from_ranking column from arena_affiliations."""
    op.drop_column("arena_affiliations", "exclude_from_ranking")
