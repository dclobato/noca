"""Add arena_submissions, arena_submission_judgments, arena_submission_test_results,
arena_problem_solvers, and arena_problem_tried tables.

Revision ID: 202605150001
Revises: 202605140002
Create Date: 2026-05-15 00:01:00.000000
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605150001"
down_revision: str | Sequence[str] | None = "202605140002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create arena submission and user progress tables."""
    op.create_table(
        "arena_submissions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("arena_users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "problem_id",
            sa.String(36),
            sa.ForeignKey("arena_problems.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "language_id",
            sa.String(64),
            sa.ForeignKey("languages.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_code", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("source_size_bytes", sa.Integer(), nullable=False),
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
    op.create_index("ix_arena_submissions_user_id", "arena_submissions", ["user_id"])
    op.create_index("ix_arena_submissions_problem_id", "arena_submissions", ["problem_id"])
    op.create_index("ix_arena_submissions_language_id", "arena_submissions", ["language_id"])
    op.create_index("ix_arena_submissions_source_hash", "arena_submissions", ["source_hash"])

    op.create_table(
        "arena_submission_judgments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "submission_id",
            sa.String(36),
            sa.ForeignKey("arena_submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("autojudge_verdict", sa.String(10), nullable=True),
        sa.Column("final_verdict", sa.String(10), nullable=True),
        sa.Column("compile_log", sa.Text(), nullable=True),
        sa.Column("max_wall_time_ms", sa.Integer(), nullable=True),
        sa.Column("max_memory_kb", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.String(200), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_arena_submission_judgments_submission_id",
        "arena_submission_judgments",
        ["submission_id"],
    )

    op.create_table(
        "arena_submission_test_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "judgment_id",
            sa.String(36),
            sa.ForeignKey("arena_submission_judgments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "test_case_id",
            sa.String(36),
            sa.ForeignKey("arena_test_cases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("verdict", sa.String(10), nullable=False),
        sa.Column("wall_time_ms", sa.Integer(), nullable=True),
        sa.Column("memory_kb", sa.Integer(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stdout_excerpt", sa.Text(), nullable=True),
        sa.Column("stderr_excerpt", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("judgment_id", name="uq_arena_submission_test_results_judgment"),
    )
    op.create_index(
        "ix_arena_submission_test_results_judgment_id",
        "arena_submission_test_results",
        ["judgment_id"],
    )
    op.create_index(
        "ix_arena_submission_test_results_test_case_id",
        "arena_submission_test_results",
        ["test_case_id"],
    )

    op.create_table(
        "arena_problem_solvers",
        sa.Column(
            "problem_id",
            sa.String(36),
            sa.ForeignKey("arena_problems.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("arena_users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("solved_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "arena_problem_tried",
        sa.Column(
            "problem_id",
            sa.String(36),
            sa.ForeignKey("arena_problems.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("arena_users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("last_tried_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    """Drop arena submission and user progress tables."""
    op.drop_table("arena_problem_tried")
    op.drop_table("arena_problem_solvers")
    op.drop_index(
        "ix_arena_submission_test_results_test_case_id",
        table_name="arena_submission_test_results",
    )
    op.drop_index(
        "ix_arena_submission_test_results_judgment_id",
        table_name="arena_submission_test_results",
    )
    op.drop_table("arena_submission_test_results")
    op.drop_index(
        "ix_arena_submission_judgments_submission_id",
        table_name="arena_submission_judgments",
    )
    op.drop_table("arena_submission_judgments")
    op.drop_index("ix_arena_submissions_source_hash", table_name="arena_submissions")
    op.drop_index("ix_arena_submissions_language_id", table_name="arena_submissions")
    op.drop_index("ix_arena_submissions_problem_id", table_name="arena_submissions")
    op.drop_index("ix_arena_submissions_user_id", table_name="arena_submissions")
    op.drop_table("arena_submissions")
