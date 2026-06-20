#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Use a sequential BIGINT primary key for Arena login history.

Revision ID: 202606140002
Revises: 202606140001
Create Date: 2026-06-14 01:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606140002"
down_revision: str | Sequence[str] | None = "202606140001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace UUID login-history IDs with generated BIGINT IDs."""
    op.add_column(
        "arena_login_history",
        sa.Column(
            "new_id",
            sa.BigInteger(),
            sa.Identity(),
            nullable=False,
        ),
    )
    op.drop_constraint(
        "arena_login_history_pkey",
        "arena_login_history",
        type_="primary",
    )
    op.drop_column("arena_login_history", "id")
    op.alter_column(
        "arena_login_history",
        "new_id",
        new_column_name="id",
    )
    op.create_primary_key(
        "arena_login_history_pkey",
        "arena_login_history",
        ["id"],
    )
    op.drop_index(
        "ix_arena_login_history_arena_user_id",
        table_name="arena_login_history",
    )


def downgrade() -> None:
    """Restore a string primary key while preserving generated ID values."""
    op.create_index(
        "ix_arena_login_history_arena_user_id",
        "arena_login_history",
        ["arena_user_id"],
    )
    op.drop_constraint(
        "arena_login_history_pkey",
        "arena_login_history",
        type_="primary",
    )
    op.execute("ALTER TABLE arena_login_history ALTER COLUMN id DROP IDENTITY IF EXISTS")
    op.alter_column(
        "arena_login_history",
        "id",
        existing_type=sa.BigInteger(),
        type_=sa.String(length=36),
        postgresql_using="id::text",
    )
    op.create_primary_key(
        "arena_login_history_pkey",
        "arena_login_history",
        ["id"],
    )
