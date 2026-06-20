#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""add max_output_bytes to arena_submission_judgments

Revision ID: 202606100001
Revises: 202606070002
Create Date: 2026-06-10 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606100001"
down_revision: str | Sequence[str] | None = "202606070002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "arena_submission_judgments",
        sa.Column(
            "max_output_bytes",
            sa.Integer(),
            nullable=True,
            comment="Peak stdout size in bytes produced by the solution across test cases.",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("arena_submission_judgments", "max_output_bytes")
