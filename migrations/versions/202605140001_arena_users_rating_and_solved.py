"""Add rating and solved_problems columns to arena_users.

Revision ID: 202605140001
Revises: 202605080001
Create Date: 2026-05-14 00:01:00.000000
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605140001"
down_revision: str | Sequence[str] | None = "202605080001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add dta_rating_update, user_rating, and solved_problems to arena_users."""
    op.add_column(
        "arena_users",
        sa.Column("dta_rating_update", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "arena_users",
        sa.Column(
            "user_rating",
            sa.Integer(),
            nullable=True,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "arena_users",
        sa.Column(
            "solved_problems",
            sa.Integer(),
            nullable=True,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    """Remove dta_rating_update, user_rating, and solved_problems from arena_users."""
    op.drop_column("arena_users", "solved_problems")
    op.drop_column("arena_users", "user_rating")
    op.drop_column("arena_users", "dta_rating_update")
