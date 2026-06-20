"""Add arena_problems, arena_test_cases, and arena_problem_ratings tables.

Revision ID: 202605140002
Revises: 202605140001
Create Date: 2026-05-14 00:02:00.000000
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605140002"
down_revision: str | Sequence[str] | None = "202605140001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create arena_problems, arena_test_cases, and arena_problem_ratings."""
    op.create_table(
        "arena_problems",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("time_limit_ms", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("memory_limit_kb", sa.Integer(), nullable=False, server_default="262144"),
        sa.Column("pids_limit", sa.Integer(), nullable=False, server_default="64"),
        sa.Column("output_limit_in_bytes", sa.Integer(), nullable=False, server_default="65536"),
        sa.Column(
            "author_id",
            sa.String(36),
            sa.ForeignKey("arena_users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source", sa.String(256), nullable=True),
        sa.Column("problem_statement", sa.Text(), nullable=False),
        sa.Column("problem_image_base64", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_arena_problems_author_id", "arena_problems", ["author_id"])

    op.create_table(
        "arena_test_cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "problem_id",
            sa.String(36),
            sa.ForeignKey("arena_problems.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("is_sample", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("input_content", sa.Text(), nullable=True),
        sa.Column("output_content", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("problem_id", "ordinal", name="uq_arena_test_cases_problem_ordinal"),
        sa.CheckConstraint("ordinal >= 1", name="ck_arena_test_cases_ordinal_positive"),
    )
    op.create_index("ix_arena_test_cases_problem_id", "arena_test_cases", ["problem_id"])

    op.create_table(
        "arena_problem_ratings",
        sa.Column(
            "problem_id",
            sa.String(36),
            sa.ForeignKey("arena_problems.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("attempted_users", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("solved_users", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_submissions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tries_before_solve", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rating", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("dta_rating_update", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("rating BETWEEN 0 AND 10", name="ck_arena_problem_ratings_rating_range"),
        sa.CheckConstraint("attempted_users >= 0", name="ck_arena_problem_ratings_attempted_users"),
        sa.CheckConstraint("solved_users >= 0", name="ck_arena_problem_ratings_solved_users"),
        sa.CheckConstraint(
            "total_submissions >= 0",
            name="ck_arena_problem_ratings_total_submissions",
        ),
        sa.CheckConstraint(
            "total_tries_before_solve >= 0",
            name="ck_arena_problem_ratings_total_tries",
        ),
        sa.CheckConstraint(
            "solved_users <= attempted_users",
            name="ck_arena_problem_ratings_solved_lte_attempted",
        ),
        sa.CheckConstraint(
            "total_tries_before_solve >= solved_users",
            name="ck_arena_problem_ratings_tries_gte_solved",
        ),
    )


def downgrade() -> None:
    """Drop arena_problem_ratings, arena_test_cases, and arena_problems."""
    op.drop_table("arena_problem_ratings")
    op.drop_index("ix_arena_test_cases_problem_id", table_name="arena_test_cases")
    op.drop_table("arena_test_cases")
    op.drop_index("ix_arena_problems_author_id", table_name="arena_problems")
    op.drop_table("arena_problems")
