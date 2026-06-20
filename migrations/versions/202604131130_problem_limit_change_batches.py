"""problem limit change batches

Revision ID: 202604131130
Revises: 202604091730
Create Date: 2026-04-13 11:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "202604131130"
down_revision: str | Sequence[str] | None = "202604091730"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_VERDICT_VALUES = ("AC", "PE", "WA", "TLE", "MLE", "OLE", "RE", "CE")


def _verdict_enum_ref() -> postgresql.ENUM:
    """Reference the existing PostgreSQL verdict enum without recreating it."""
    return postgresql.ENUM(*_VERDICT_VALUES, name="verdict", create_type=False)


def upgrade() -> None:
    op.create_table(
        "problem_limit_change_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("contest_id", sa.String(length=36), nullable=False),
        sa.Column("problem_id", sa.String(length=36), nullable=False),
        sa.Column("triggered_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("triggered_by_uberadmin_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["contest_id"], ["contests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["problem_id"], ["problems.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["triggered_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["triggered_by_uberadmin_id"], ["uber_admins.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_problem_limit_change_batches_contest_id"),
        "problem_limit_change_batches",
        ["contest_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_problem_limit_change_batches_problem_id"),
        "problem_limit_change_batches",
        ["problem_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_problem_limit_change_batches_triggered_by_user_id"),
        "problem_limit_change_batches",
        ["triggered_by_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_problem_limit_change_batches_triggered_by_uberadmin_id"),
        "problem_limit_change_batches",
        ["triggered_by_uberadmin_id"],
        unique=False,
    )

    op.create_table(
        "problem_limit_change_batch_languages",
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("language_id", sa.String(length=64), nullable=False),
        sa.Column("change_kind", sa.String(length=16), nullable=False),
        sa.Column("before_limits", sa.JSON(), nullable=False),
        sa.Column("after_limits", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "change_kind IN ('explicit', 'fallback')",
            name="ck_problem_limit_change_batch_languages_change_kind",
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["problem_limit_change_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["language_id"], ["languages.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("batch_id", "language_id"),
    )

    op.create_table(
        "problem_limit_change_batch_submissions",
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("submission_id", sa.String(length=36), nullable=False),
        sa.Column("language_id", sa.String(length=64), nullable=False),
        sa.Column("original_judgment_id", sa.String(length=36), nullable=False),
        sa.Column("original_final_verdict", _verdict_enum_ref(), nullable=False),
        sa.Column("rejudge_status", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("queued_judgment_id", sa.String(length=36), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "rejudge_status IN ('PENDING', 'QUEUED', 'STALE')",
            name="ck_problem_limit_change_batch_submissions_rejudge_status",
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["problem_limit_change_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["language_id"], ["languages.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["original_judgment_id"], ["submission_judgments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["queued_judgment_id"], ["submission_judgments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("batch_id", "submission_id"),
    )
    op.create_index(
        op.f("ix_problem_limit_change_batch_submissions_language_id"),
        "problem_limit_change_batch_submissions",
        ["language_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_problem_limit_change_batch_submissions_original_judgment_id"),
        "problem_limit_change_batch_submissions",
        ["original_judgment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_problem_limit_change_batch_submissions_queued_judgment_id"),
        "problem_limit_change_batch_submissions",
        ["queued_judgment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_problem_limit_change_batch_submissions_queued_judgment_id"),
        table_name="problem_limit_change_batch_submissions",
    )
    op.drop_index(
        op.f("ix_problem_limit_change_batch_submissions_original_judgment_id"),
        table_name="problem_limit_change_batch_submissions",
    )
    op.drop_index(
        op.f("ix_problem_limit_change_batch_submissions_language_id"),
        table_name="problem_limit_change_batch_submissions",
    )
    op.drop_table("problem_limit_change_batch_submissions")
    op.drop_table("problem_limit_change_batch_languages")
    op.drop_index(
        op.f("ix_problem_limit_change_batches_triggered_by_uberadmin_id"),
        table_name="problem_limit_change_batches",
    )
    op.drop_index(
        op.f("ix_problem_limit_change_batches_triggered_by_user_id"),
        table_name="problem_limit_change_batches",
    )
    op.drop_index(op.f("ix_problem_limit_change_batches_problem_id"), table_name="problem_limit_change_batches")
    op.drop_index(op.f("ix_problem_limit_change_batches_contest_id"), table_name="problem_limit_change_batches")
    op.drop_table("problem_limit_change_batches")
