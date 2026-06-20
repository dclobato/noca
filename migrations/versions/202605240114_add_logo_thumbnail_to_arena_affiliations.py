#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add logo_thumbnail_base64 to arena_affiliations.

Revision ID: 202605240114
Revises: 202605240113
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605240114"
down_revision: str | Sequence[str] | None = "202605240113"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add logo_thumbnail_base64 column to arena_affiliations."""
    op.add_column(
        "arena_affiliations",
        sa.Column(
            "logo_thumbnail_base64",
            sa.Text(),
            nullable=True,
            comment="Affiliation logo thumbnail (64x64 px) stored as base64-encoded image bytes",
        ),
    )


def downgrade() -> None:
    """Remove logo_thumbnail_base64 column from arena_affiliations."""
    op.drop_column("arena_affiliations", "logo_thumbnail_base64")
