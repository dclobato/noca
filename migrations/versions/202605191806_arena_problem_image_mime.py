"""arena: Add problem image MIME type.

Revision ID: 202605191806
Revises: a5c71ab130e5
Create Date: 2026-05-19 18:06:00.000000

"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605191806"
down_revision: str | Sequence[str] | None = "a5c71ab130e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "arena_problems",
        sa.Column(
            "problem_image_mime",
            sa.String(length=129),
            nullable=True,
            comment="MIME type of the problem statement image, used to serve the image correctly.",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("arena_problems", "problem_image_mime")
