"""web: Add problem illustration image to contest problems.

Revision ID: 202607120001
Revises: 202607110001
Create Date: 2026-07-12 00:01:00.000000

"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607120001"
down_revision: str | Sequence[str] | None = "202607110001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "problems",
        sa.Column(
            "problem_image_base64",
            sa.Text(),
            nullable=True,
            comment="BASE-64 encoded image for the statement.",
        ),
    )
    op.add_column(
        "problems",
        sa.Column(
            "problem_image_mime",
            sa.String(length=129),
            nullable=True,
            comment="MIME type of the problem statement image, used to serve the image correctly.",
        ),
    )
    op.add_column(
        "problems",
        sa.Column(
            "problem_image_caption",
            sa.String(length=512),
            nullable=True,
            comment="Optional caption displayed below the problem image.",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("problems", "problem_image_caption")
    op.drop_column("problems", "problem_image_mime")
    op.drop_column("problems", "problem_image_base64")
