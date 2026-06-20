"""add problem profiling tables and repetitions

Revision ID: b7d4e2f91c30
Revises: a3f2b1c9d4e6
Create Date: 2026-04-09 14:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from shared.language_registry import default_language_seed_rows

# revision identifiers, used by Alembic.
revision: str = "b7d4e2f91c30"
down_revision: str | Sequence[str] | None = "a3f2b1c9d4e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROFILING_STATUS_VALUES = ("QUEUED", "DISPATCHED", "RUNNING", "DONE", "FAILED")
_VERDICT_VALUES = ("AC", "PE", "WA", "TLE", "MLE", "OLE", "RE", "CE")


def _create_profiling_status_enum() -> postgresql.ENUM:
    return postgresql.ENUM(*_PROFILING_STATUS_VALUES, name="profilingstatus")


def _profiling_status_enum_ref() -> postgresql.ENUM:
    return postgresql.ENUM(*_PROFILING_STATUS_VALUES, name="profilingstatus", create_type=False)


def _verdict_enum_ref() -> postgresql.ENUM:
    return postgresql.ENUM(*_VERDICT_VALUES, name="verdict", create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    _create_profiling_status_enum().create(bind, checkfirst=True)

    op.add_column(
        "languages",
        sa.Column(
            "profiling_repetitions_default",
            sa.Integer(),
            nullable=True,
            comment="Default repetition count to use when auto-profiling this language",
        ),
    )
    op.add_column(
        "languages",
        sa.Column(
            "profiled_pids_floor",
            sa.Integer(),
            nullable=True,
            comment="Minimum PID limit to persist when auto-profiling this language",
        ),
    )

    languages_table = sa.table(
        "languages",
        sa.column("id"),
        sa.column("profiling_repetitions_default"),
        sa.column("profiled_pids_floor"),
    )
    for row in default_language_seed_rows():
        bind.execute(
            sa.update(languages_table)
            .where(languages_table.c.id == row["id"])
            .values(
                profiling_repetitions_default=row["profiling_repetitions_default"],
                profiled_pids_floor=row["profiled_pids_floor"],
            )
        )

    op.alter_column("languages", "profiling_repetitions_default", nullable=False)
    op.alter_column("languages", "profiled_pids_floor", nullable=False)
    op.create_check_constraint(
        "ck_languages_profiling_repetitions_default_positive",
        "languages",
        "profiling_repetitions_default >= 1",
    )
    op.create_check_constraint(
        "ck_languages_profiled_pids_floor_positive",
        "languages",
        "profiled_pids_floor >= 1",
    )

    op.add_column(
        "problems",
        sa.Column(
            "repetitions",
            sa.Integer(),
            server_default="1",
            nullable=False,
            comment="Cumulative time budget divided across this many runs per test case",
        ),
    )
    op.create_check_constraint(
        "ck_problems_repetitions_positive",
        "problems",
        "repetitions >= 1",
    )

    op.create_table(
        "profiling_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("problem_id", sa.String(length=36), nullable=False),
        sa.Column("language_id", sa.String(length=64), nullable=False),
        sa.Column("source_code", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("status", _profiling_status_enum_ref(), server_default="QUEUED", nullable=False),
        sa.Column("safety_factor", sa.Float(), server_default="1.5", nullable=False),
        sa.Column("worker_id", sa.String(length=200), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("compile_log", sa.Text(), nullable=True),
        sa.Column("triggered_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["language_id"], ["languages.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["problem_id"], ["problems.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["triggered_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_profiling_runs_language_id"), "profiling_runs", ["language_id"], unique=False)
    op.create_index(op.f("ix_profiling_runs_problem_id"), "profiling_runs", ["problem_id"], unique=False)
    op.create_index(
        op.f("ix_profiling_runs_triggered_by_user_id"),
        "profiling_runs",
        ["triggered_by_user_id"],
        unique=False,
    )

    op.create_table(
        "profiling_case_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("profiling_run_id", sa.String(length=36), nullable=False),
        sa.Column("test_case_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("total_wall_time_ms", sa.Integer(), nullable=True),
        sa.Column("peak_memory_kb", sa.Integer(), nullable=True),
        sa.Column("peak_output_bytes", sa.Integer(), nullable=True),
        sa.Column("peak_pids", sa.Integer(), nullable=True),
        sa.Column("verdict", _verdict_enum_ref(), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["profiling_run_id"], ["profiling_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["test_case_id"], ["test_cases.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profiling_run_id", "test_case_id", name="uq_profiling_case_results_case"),
    )
    op.create_index(
        op.f("ix_profiling_case_results_profiling_run_id"),
        "profiling_case_results",
        ["profiling_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_profiling_case_results_test_case_id"),
        "profiling_case_results",
        ["test_case_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_profiling_case_results_test_case_id"), table_name="profiling_case_results")
    op.drop_index(op.f("ix_profiling_case_results_profiling_run_id"), table_name="profiling_case_results")
    op.drop_table("profiling_case_results")

    op.drop_index(op.f("ix_profiling_runs_triggered_by_user_id"), table_name="profiling_runs")
    op.drop_index(op.f("ix_profiling_runs_problem_id"), table_name="profiling_runs")
    op.drop_index(op.f("ix_profiling_runs_language_id"), table_name="profiling_runs")
    op.drop_table("profiling_runs")

    op.drop_constraint("ck_problems_repetitions_positive", "problems", type_="check")
    op.drop_column("problems", "repetitions")

    op.drop_constraint("ck_languages_profiled_pids_floor_positive", "languages", type_="check")
    op.drop_constraint("ck_languages_profiling_repetitions_default_positive", "languages", type_="check")
    op.drop_column("languages", "profiled_pids_floor")
    op.drop_column("languages", "profiling_repetitions_default")

    bind = op.get_bind()
    _create_profiling_status_enum().drop(bind, checkfirst=True)
