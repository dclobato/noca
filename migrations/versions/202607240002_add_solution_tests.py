"""shared: Non-scoring solution-test runs and their case results.

Revision ID: 202607240002
Revises: 202607240001
Create Date: 2026-07-24 10:00:00.000000

``solution_test_case_results`` joins the high-churn pipeline autovacuum set: a
retry deletes and rewrites every row of a run wholesale, which is the same churn
profile ``profiling_case_results`` already has. ``solution_test_runs`` mirrors
``profiling_runs`` — staff-triggered volume with a handful of lifecycle UPDATEs
per row — and ``profiling_runs`` is deliberately left on the server-wide
defaults, so this table is too.
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202607240002"
down_revision: str | Sequence[str] | None = "202607240001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JUDGMENT_STATUS_VALUES = ("QUEUED", "DISPATCHED", "JUDGING", "DONE", "FAILED")
_VERDICT_VALUES = ("AC", "PE", "WA", "TLE", "MLE", "OLE", "RE", "CE")


def _judgment_status_enum_ref() -> postgresql.ENUM:
    return postgresql.ENUM(*_JUDGMENT_STATUS_VALUES, name="judgmentstatus", create_type=False)


def _verdict_enum_ref() -> postgresql.ENUM:
    return postgresql.ENUM(*_VERDICT_VALUES, name="verdict", create_type=False)


_PIPELINE_SETTINGS = {
    "autovacuum_vacuum_scale_factor": "0.05",
    "autovacuum_vacuum_threshold": "200",
    "autovacuum_analyze_scale_factor": "0.02",
    "autovacuum_analyze_threshold": "200",
}


def upgrade() -> None:
    """Create the solution-test tables and tune the case-results table."""
    op.create_table(
        "solution_test_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("problem_id", sa.String(length=36), nullable=False),
        sa.Column("language_id", sa.String(length=64), nullable=False),
        sa.Column("source_code", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("source_size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            _judgment_status_enum_ref(),
            nullable=False,
            server_default="QUEUED",
            comment="Status machine mirrors submissions so the worker state machine is reused verbatim.",
        ),
        sa.Column(
            "verdict",
            _verdict_enum_ref(),
            nullable=True,
            comment="Always final: solution tests are never human-confirmed and never scored.",
        ),
        sa.Column("compile_log", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("max_wall_time_ms", sa.Integer(), nullable=True),
        sa.Column("max_memory_kb", sa.Integer(), nullable=True),
        sa.Column("worker_id", sa.String(length=200), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("triggered_by_uberadmin_id", sa.String(length=36), nullable=True),
        sa.Column(
            "triggered_by_label",
            sa.String(length=255),
            nullable=False,
            comment="Actor login snapshotted at creation; preserves attribution after the account is deleted.",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["problem_id"], ["problems.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["language_id"], ["languages.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["triggered_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["triggered_by_uberadmin_id"], ["uber_admins.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "NOT (triggered_by_user_id IS NOT NULL AND triggered_by_uberadmin_id IS NOT NULL)",
            name="ck_solution_test_runs_at_most_one_actor",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_solution_test_runs_problem_id", "solution_test_runs", ["problem_id"])
    op.create_index("ix_solution_test_runs_triggered_by_user_id", "solution_test_runs", ["triggered_by_user_id"])
    op.create_index(
        "ix_solution_test_runs_triggered_by_uberadmin_id",
        "solution_test_runs",
        ["triggered_by_uberadmin_id"],
    )
    op.create_index(
        "ix_solution_test_runs_problem_created_at",
        "solution_test_runs",
        ["problem_id", "created_at"],
    )

    op.create_table(
        "solution_test_case_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("solution_test_run_id", sa.String(length=36), nullable=False),
        sa.Column(
            "test_case_id",
            sa.String(length=36),
            nullable=True,
            comment="Nulled when the test case is deleted; the run's history survives.",
        ),
        sa.Column(
            "ordinal",
            sa.Integer(),
            nullable=False,
            comment="Immutable; retained after the test case is deleted.",
        ),
        sa.Column(
            "attempt_number",
            sa.Integer(),
            nullable=True,
            comment="Set only for interactive (custom-validator) rows; NULL for ordinary test-case rows.",
        ),
        sa.Column("verdict", _verdict_enum_ref(), nullable=False),
        sa.Column("wall_time_ms", sa.Integer(), nullable=True),
        sa.Column("memory_kb", sa.Integer(), nullable=True),
        sa.Column("output_bytes", sa.Integer(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("exit_signal", sa.Integer(), nullable=True),
        sa.Column(
            "input_excerpt",
            sa.Text(),
            nullable=True,
            comment="Executed problem input snapshot, bounded to 10 KiB; NULL for interactive rows.",
        ),
        sa.Column(
            "expected_output_excerpt",
            sa.Text(),
            nullable=True,
            comment="Executed expected-output snapshot, bounded to 10 KiB; NULL for interactive rows.",
        ),
        sa.Column("stdout_excerpt", sa.Text(), nullable=True),
        sa.Column("stderr_excerpt", sa.Text(), nullable=True),
        sa.Column(
            "transcript",
            sa.JSON(),
            nullable=True,
            comment="Interactive rows only: {'lines': [{'dir', 'line', 'partial'?}], 'truncated': bool}.",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["solution_test_run_id"], ["solution_test_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["test_case_id"], ["test_cases.id"], ondelete="SET NULL"),
        sa.CheckConstraint("ordinal >= 1", name="ck_solution_test_case_results_ordinal_positive"),
        sa.CheckConstraint(
            "attempt_number IS NULL OR attempt_number IN (1, 2)",
            name="ck_solution_test_case_results_attempt_number",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_solution_test_case_results_solution_test_run_id",
        "solution_test_case_results",
        ["solution_test_run_id"],
    )
    op.create_index("ix_solution_test_case_results_test_case_id", "solution_test_case_results", ["test_case_id"])
    op.create_index(
        "uq_solution_test_case_results_ordinary",
        "solution_test_case_results",
        ["solution_test_run_id", "ordinal"],
        unique=True,
        postgresql_where=sa.text("attempt_number IS NULL"),
    )
    op.create_index(
        "uq_solution_test_case_results_interactive",
        "solution_test_case_results",
        ["solution_test_run_id", "ordinal", "attempt_number"],
        unique=True,
        postgresql_where=sa.text("attempt_number IS NOT NULL"),
    )

    params = ", ".join(f"{key} = {value}" for key, value in _PIPELINE_SETTINGS.items())
    op.execute(f"ALTER TABLE solution_test_case_results SET ({params})")


def downgrade() -> None:
    """Drop the solution-test tables."""
    op.drop_table("solution_test_case_results")
    op.drop_table("solution_test_runs")
