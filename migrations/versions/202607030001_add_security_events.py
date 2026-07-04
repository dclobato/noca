#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add cross-module security events.

Revision ID: 202607030001
Revises: 202606220007
Create Date: 2026-07-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607030001"
down_revision: str | Sequence[str] | None = "202606220007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the security_events table."""
    op.create_table(
        "security_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("module", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=96), nullable=False),
        sa.Column("severity", sa.String(length=24), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("identifier_hash", sa.String(length=64), nullable=True),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), server_default="{}", nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_security_events_created_at", "security_events", ["created_at"])
    op.create_index("ix_security_events_type_created_at", "security_events", ["event_type", "created_at"])
    op.create_index("ix_security_events_module_created_at", "security_events", ["module", "created_at"])


def downgrade() -> None:
    """Drop the security_events table."""
    op.drop_index("ix_security_events_module_created_at", table_name="security_events")
    op.drop_index("ix_security_events_type_created_at", table_name="security_events")
    op.drop_index("ix_security_events_created_at", table_name="security_events")
    op.drop_table("security_events")
