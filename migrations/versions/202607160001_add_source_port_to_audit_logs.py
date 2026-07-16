"""Add source_port to login and security audit logs.

Revision ID: 202607160001
Revises: 202607150001
Create Date: 2026-07-16 00:01:00.000000
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607160001"
down_revision: str | Sequence[str] | None = "202607150001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("login_history", sa.Column("source_port", sa.Integer(), nullable=True))
    op.add_column("arena_login_history", sa.Column("source_port", sa.Integer(), nullable=True))
    op.add_column("security_events", sa.Column("source_port", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("security_events", "source_port")
    op.drop_column("arena_login_history", "source_port")
    op.drop_column("login_history", "source_port")
