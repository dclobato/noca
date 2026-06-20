#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Add the Arena login-history user and timestamp index.

Revision ID: 202606140001
Revises: 202606130001
Create Date: 2026-06-14 00:01:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606140001"
down_revision: str | Sequence[str] | None = "202606130001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_arena_login_history_user_login",
        "arena_login_history",
        ["arena_user_id", "dta_login"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_arena_login_history_user_login",
        table_name="arena_login_history",
    )
