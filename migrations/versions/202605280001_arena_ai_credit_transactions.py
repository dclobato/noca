#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Arena AI credit transaction history table

Revision ID: 202605280001
Revises: 3dd3ca1867a9
Create Date: 2026-05-28 00:01:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202605280001"
down_revision: Union[str, Sequence[str], None] = "3dd3ca1867a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "arena_ai_credit_transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("arena_users.id", ondelete="CASCADE"),
            nullable=False,
            comment="FK to arena_users. The user whose credit balance changed.",
        ),
        sa.Column(
            "amount",
            sa.Integer(),
            nullable=False,
            comment="Credit delta: positive for top-up, negative for consumption (typically -1).",
        ),
        sa.Column(
            "balance_after",
            sa.Integer(),
            nullable=False,
            comment="Snapshot of ai_backend_credits immediately after this transaction.",
        ),
        sa.Column(
            "transaction_type",
            sa.String(20),
            nullable=False,
            comment="'topup' when an admin adds credits; 'consumption' when a review consumes one.",
        ),
        sa.Column(
            "submission_id",
            sa.String(36),
            sa.ForeignKey("arena_submissions.id", ondelete="SET NULL"),
            nullable=True,
            comment="FK to arena_submissions. Set only for consumption transactions.",
        ),
        sa.Column(
            "admin_id",
            sa.String(36),
            sa.ForeignKey("arena_users.id", ondelete="SET NULL"),
            nullable=True,
            comment="FK to arena_users. Set only for top-up transactions (the admin who acted).",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_arena_ai_credit_transactions_user_id",
        "arena_ai_credit_transactions",
        ["user_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_arena_ai_credit_transactions_user_id", table_name="arena_ai_credit_transactions")
    op.drop_table("arena_ai_credit_transactions")
