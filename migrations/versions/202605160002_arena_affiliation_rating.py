#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add rating and dta_rating_update to arena_affiliations.

Revision ID: 202605160002
Revises: 202605160001
Create Date: 2026-05-16
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605160002"
down_revision: str | Sequence[str] | None = "202605160001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add rating and dta_rating_update columns to arena_affiliations."""
    op.add_column(
        "arena_affiliations",
        sa.Column(
            "rating",
            sa.Integer(),
            nullable=True,
            server_default=sa.text("0"),
            comment="Affiliation combined rating computed from member scores using geometric weighting",
        ),
    )
    op.add_column(
        "arena_affiliations",
        sa.Column(
            "dta_rating_update",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp when the affiliation rating was last computed",
        ),
    )


def downgrade() -> None:
    """Remove rating and dta_rating_update columns from arena_affiliations."""
    op.drop_column("arena_affiliations", "dta_rating_update")
    op.drop_column("arena_affiliations", "rating")
