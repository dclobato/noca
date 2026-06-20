#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add Arena rating history tables.

Revision ID: 202605170001
Revises: 202605160002
Create Date: 2026-05-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605170001"
down_revision: str | Sequence[str] | None = "202605160002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "arena_problem_rating_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "problem_id",
            sa.String(36),
            sa.ForeignKey("arena_problems.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_arena_problem_rating_history_problem_id",
        "arena_problem_rating_history",
        ["problem_id"],
    )

    op.create_table(
        "arena_user_rating_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("arena_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_arena_user_rating_history_user_id",
        "arena_user_rating_history",
        ["user_id"],
    )

    op.create_table(
        "arena_affiliation_rating_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "affiliation_id",
            sa.String(36),
            sa.ForeignKey("arena_affiliations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_arena_affiliation_rating_history_affiliation_id",
        "arena_affiliation_rating_history",
        ["affiliation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_arena_affiliation_rating_history_affiliation_id",
        table_name="arena_affiliation_rating_history",
    )
    op.drop_table("arena_affiliation_rating_history")

    op.drop_index(
        "ix_arena_user_rating_history_user_id",
        table_name="arena_user_rating_history",
    )
    op.drop_table("arena_user_rating_history")

    op.drop_index(
        "ix_arena_problem_rating_history_problem_id",
        table_name="arena_problem_rating_history",
    )
    op.drop_table("arena_problem_rating_history")
