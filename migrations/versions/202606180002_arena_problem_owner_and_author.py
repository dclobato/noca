#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Separate Arena problem ownership from authorship.

Revision ID: 202606180002
Revises: 202606180001
Create Date: 2026-06-18 00:02:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202606180002"
down_revision: str | None = "202606180001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename the user relation to owner and add explicit authorship fields."""
    op.drop_index("ix_arena_problems_author_id", table_name="arena_problems")
    op.drop_constraint(
        "arena_problems_author_id_fkey",
        "arena_problems",
        type_="foreignkey",
    )
    op.alter_column("arena_problems", "author_id", new_column_name="owner_id")
    op.create_foreign_key(
        "arena_problems_owner_id_fkey",
        "arena_problems",
        "arena_users",
        ["owner_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_arena_problems_owner_id", "arena_problems", ["owner_id"])
    op.add_column(
        "arena_problems",
        sa.Column("author", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "arena_problems",
        sa.Column(
            "author_is_owner",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.create_check_constraint(
        "ck_arena_problems_author_choice",
        "arena_problems",
        "(author_is_owner AND author IS NULL) OR "
        "(NOT author_is_owner AND author IS NOT NULL "
        "AND length(trim(author)) BETWEEN 1 AND 80)",
    )


def downgrade() -> None:
    """Restore the original user-backed author relation."""
    op.drop_constraint(
        "ck_arena_problems_author_choice",
        "arena_problems",
        type_="check",
    )
    op.drop_column("arena_problems", "author_is_owner")
    op.drop_column("arena_problems", "author")
    op.drop_index("ix_arena_problems_owner_id", table_name="arena_problems")
    op.drop_constraint(
        "arena_problems_owner_id_fkey",
        "arena_problems",
        type_="foreignkey",
    )
    op.alter_column("arena_problems", "owner_id", new_column_name="author_id")
    op.create_foreign_key(
        "arena_problems_author_id_fkey",
        "arena_problems",
        "arena_users",
        ["author_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_arena_problems_author_id", "arena_problems", ["author_id"])
