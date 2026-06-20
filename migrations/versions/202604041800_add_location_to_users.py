"""add location column to users

Revision ID: 4a7e2f9c1b83
Revises: c716f6d638e6
Create Date: 2026-04-04 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4a7e2f9c1b83"
down_revision: Union[str, Sequence[str], None] = "c716f6d638e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column(
            "location",
            sa.String(length=16),
            nullable=True,
            comment="Physical location of the team within the site (e.g. room, lab)",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "location")
