"""Add arena_user_reputation table.

Revision ID: 202607180001
Revises: 202607160002
Create Date: 2026-07-18 00:01:00.000000
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607180001"
down_revision: str | Sequence[str] | None = "202607160002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "arena_user_reputation",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("signup_ip", sa.String(length=45), nullable=True),
        sa.Column("ip_fraud_score", sa.Integer(), nullable=True),
        sa.Column("ip_report", sa.JSON(), nullable=True),
        sa.Column("ip_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email_fraud_score", sa.Integer(), nullable=True),
        sa.Column("email_overall_score", sa.Integer(), nullable=True),
        sa.Column("email_report", sa.JSON(), nullable=True),
        sa.Column("email_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["arena_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_arena_user_reputation_user_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("arena_user_reputation")
