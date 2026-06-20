"""Add is_enabled to uber_admins.

Revision ID: 202604231600
Revises: 202604131130
Create Date: 2026-04-23 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202604231600"
down_revision: str | None = "202604131130"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the enabled flag to existing UberAdmin accounts."""
    op.add_column(
        "uber_admins",
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    """Remove the enabled flag from UberAdmin accounts."""
    op.drop_column("uber_admins", "is_enabled")
