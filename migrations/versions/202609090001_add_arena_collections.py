#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""add arena_collections and arena_problems.collection_id

A collection is an event (ICPC, Maratona SBC, InterIF) or a class (Iniciantes,
Expressoes regulares). A problem belongs to at most one, so the link is a
nullable foreign key on ``arena_problems`` rather than a junction table: the
cardinality is then enforced by the database itself. ``ON DELETE SET NULL``
unfiles a collection's problems instead of deleting them.

The table ships empty. Existing origin-style categories (icpc, maratona-sbc,
interif, ...) are deliberately left untouched; nothing is migrated here.

``arena_collections`` is low-churn reference data and the new column is written
only when a problem is edited, so the server-wide autovacuum defaults are
appropriate; no per-table storage parameters are needed.

Revision ID: 202609090001
Revises: 202609080002
Create Date: 2026-09-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202609090001"
down_revision: str | Sequence[str] | None = "202609080002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the collections table and link problems to it."""
    op.create_table(
        "arena_collections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "name",
            sa.String(length=128),
            nullable=False,
            comment="Human-readable collection name shown to users, e.g. 'Maratona SBC'.",
        ),
        sa.Column(
            "slug",
            sa.String(length=128),
            nullable=False,
            comment="URL-safe identifier for the collection (lowercase, hyphens).",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("slug"),
    )
    op.add_column(
        "arena_problems",
        sa.Column(
            "collection_id",
            sa.String(length=36),
            nullable=True,
            comment="Optional collection (event or class) this problem belongs to; at most one.",
        ),
    )
    op.create_foreign_key(
        "fk_arena_problems_collection_id",
        "arena_problems",
        "arena_collections",
        ["collection_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # The filter and the SET NULL cascade both scan by this non-leading column.
    op.create_index(
        "ix_arena_problems_collection_id",
        "arena_problems",
        ["collection_id"],
    )


def downgrade() -> None:
    """Unlink problems from collections and drop the collections table."""
    op.drop_index("ix_arena_problems_collection_id", table_name="arena_problems")
    op.drop_constraint("fk_arena_problems_collection_id", "arena_problems", type_="foreignkey")
    op.drop_column("arena_problems", "collection_id")
    op.drop_table("arena_collections")
