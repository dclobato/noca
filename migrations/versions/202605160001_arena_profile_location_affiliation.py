"""Add Arena profile location and affiliation fields.

Revision ID: 202605160001
Revises: 202605150001
Create Date: 2026-05-16 18:00:00.000000
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605160001"
down_revision: str | Sequence[str] | None = "202605150001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create affiliations and add optional location fields to Arena users."""
    op.create_table(
        "arena_affiliations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=True),
        sa.Column("subdivision_code", sa.String(16), nullable=True),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("logo_base64", sa.Text(), nullable=True),
        sa.Column("logo_mime", sa.String(129), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_arena_affiliations_name", "arena_affiliations", ["name"], unique=True)
    op.add_column("arena_users", sa.Column("country_code", sa.String(2), nullable=True))
    op.add_column("arena_users", sa.Column("subdivision_code", sa.String(16), nullable=True))
    op.add_column("arena_users", sa.Column("affiliation_id", sa.String(36), nullable=True))
    op.create_index("ix_arena_users_affiliation_id", "arena_users", ["affiliation_id"])
    op.create_foreign_key(
        "fk_arena_users_affiliation_id",
        "arena_users",
        "arena_affiliations",
        ["affiliation_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Remove Arena profile affiliation and location fields."""
    op.drop_constraint("fk_arena_users_affiliation_id", "arena_users", type_="foreignkey")
    op.drop_index("ix_arena_users_affiliation_id", table_name="arena_users")
    op.drop_column("arena_users", "affiliation_id")
    op.drop_column("arena_users", "subdivision_code")
    op.drop_column("arena_users", "country_code")
    op.drop_index("ix_arena_affiliations_name", table_name="arena_affiliations")
    op.drop_table("arena_affiliations")
