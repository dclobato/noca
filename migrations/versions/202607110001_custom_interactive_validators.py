#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add staged custom validators and interactive attempt diagnostics.

Revision ID: 202607110001
Revises: 202607100001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202607110001"
down_revision: str | None = "202607100001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE_STATE_VALUES = ("VALID", "RUNTIME_FAILED")
_CANDIDATE_STATE_VALUES = ("PENDING", "INVALID")
_CRASH_REASON_VALUES = ("SIGNAL", "STARTUP", "COMMUNICATION", "WATCHDOG")
_VERDICT_VALUES = ("AC", "PE", "WA", "TLE", "MLE", "OLE", "RE", "CE")


def _create_active_state_enum() -> postgresql.ENUM:
    return postgresql.ENUM(*_ACTIVE_STATE_VALUES, name="customvalidatoractivestate")


def _create_candidate_state_enum() -> postgresql.ENUM:
    return postgresql.ENUM(*_CANDIDATE_STATE_VALUES, name="customvalidatorcandidatestate")


def _create_crash_reason_enum() -> postgresql.ENUM:
    return postgresql.ENUM(*_CRASH_REASON_VALUES, name="customvalidatorcrashreason")


def _active_state_enum_ref() -> postgresql.ENUM:
    return postgresql.ENUM(*_ACTIVE_STATE_VALUES, name="customvalidatoractivestate", create_type=False)


def _candidate_state_enum_ref() -> postgresql.ENUM:
    return postgresql.ENUM(*_CANDIDATE_STATE_VALUES, name="customvalidatorcandidatestate", create_type=False)


def _crash_reason_enum_ref() -> postgresql.ENUM:
    return postgresql.ENUM(*_CRASH_REASON_VALUES, name="customvalidatorcrashreason", create_type=False)


def _verdict_enum_ref() -> postgresql.ENUM:
    return postgresql.ENUM(*_VERDICT_VALUES, name="verdict", create_type=False)


def _validator_columns(problem_table: str) -> list[sa.Column[object]]:
    """Return shared validator columns for one problem domain."""
    return [
        sa.Column(
            "problem_id",
            sa.String(36),
            sa.ForeignKey(f"{problem_table}.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("active_language_id", sa.String(64), sa.ForeignKey("languages.id", ondelete="RESTRICT")),
        sa.Column("active_source", sa.Text()),
        sa.Column("active_state", _active_state_enum_ref()),
        sa.Column("active_validated_at", sa.DateTime(timezone=True)),
        sa.Column("candidate_language_id", sa.String(64), sa.ForeignKey("languages.id", ondelete="RESTRICT")),
        sa.Column("candidate_source", sa.Text()),
        sa.Column("candidate_token", sa.String(36), unique=True),
        sa.Column("candidate_state", _candidate_state_enum_ref()),
        sa.Column("candidate_compile_log", sa.Text()),
        sa.Column("candidate_validated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _attempt_columns(judgment_table: str) -> list[sa.Column[object]]:
    """Return shared interactive-attempt diagnostic columns."""
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "judgment_id",
            sa.String(36),
            sa.ForeignKey(f"{judgment_table}.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("contestant_exit_code", sa.Integer()),
        sa.Column("contestant_signal", sa.Integer()),
        sa.Column("validator_exit_code", sa.Integer()),
        sa.Column("validator_signal", sa.Integer()),
        sa.Column("transcript", sa.JSON()),
        sa.Column("contestant_stderr_excerpt", sa.Text(), nullable=False, server_default=""),
        sa.Column("validator_stderr_excerpt", sa.Text(), nullable=False, server_default=""),
        sa.Column("wall_time_ms", sa.Integer()),
        sa.Column("memory_kb", sa.Integer()),
        sa.Column("output_bytes", sa.Integer()),
        sa.Column("limit_outcome", sa.String(16)),
        sa.Column("validator_verdict", _verdict_enum_ref()),
        sa.Column("crash_reason", _crash_reason_enum_ref()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    """Create validator and attempt tables for Contest and Arena."""
    bind = op.get_bind()
    _create_active_state_enum().create(bind, checkfirst=True)
    _create_candidate_state_enum().create(bind, checkfirst=True)
    _create_crash_reason_enum().create(bind, checkfirst=True)

    for table_name, problem_table, prefix in (
        ("problem_custom_validators", "problems", "problem_custom_validator"),
        ("arena_problem_custom_validators", "arena_problems", "arena_custom_validator"),
    ):
        op.create_table(
            table_name,
            *_validator_columns(problem_table),
            sa.CheckConstraint(
                "(active_language_id IS NULL AND active_source IS NULL AND active_state IS NULL "
                "AND active_validated_at IS NULL) OR (active_language_id IS NOT NULL AND active_source "
                "IS NOT NULL AND active_state IS NOT NULL AND active_validated_at IS NOT NULL)",
                name=f"ck_{prefix}_active_complete",
            ),
            sa.CheckConstraint(
                "(candidate_language_id IS NULL AND candidate_source IS NULL AND candidate_token IS NULL "
                "AND candidate_state IS NULL AND candidate_compile_log IS NULL AND candidate_validated_at IS NULL) "
                "OR (candidate_language_id IS NOT NULL AND candidate_source IS NOT NULL AND candidate_token "
                "IS NOT NULL AND ((candidate_state = 'PENDING' AND candidate_compile_log IS NULL AND "
                "candidate_validated_at IS NULL) OR (candidate_state = 'INVALID' AND candidate_compile_log "
                "IS NOT NULL AND candidate_validated_at IS NOT NULL)))",
                name=f"ck_{prefix}_candidate_complete",
            ),
        )

    for table_name, judgment_table, unique_name, check_prefix in (
        ("submission_interactive_attempts", "submission_judgments", "uq_submission_interactive_attempt", "submission"),
        (
            "arena_submission_interactive_attempts",
            "arena_submission_judgments",
            "uq_arena_submission_interactive_attempt",
            "arena",
        ),
    ):
        op.create_table(
            table_name,
            *_attempt_columns(judgment_table),
            sa.UniqueConstraint("judgment_id", "attempt_number", name=unique_name),
            sa.CheckConstraint("attempt_number IN (1, 2)", name=f"ck_{check_prefix}_interactive_attempt_number"),
            sa.CheckConstraint(
                "NOT (validator_verdict IS NOT NULL AND crash_reason IS NOT NULL)",
                name=f"ck_{check_prefix}_interactive_outcome_exclusive",
            ),
            sa.CheckConstraint(
                "limit_outcome IS NULL OR limit_outcome IN ('MLE', 'OLE')",
                name=f"ck_{check_prefix}_limit_outcome",
            ),
            sa.CheckConstraint(
                "validator_verdict IS NULL OR validator_verdict IN ('AC', 'WA', 'TLE', 'PE', 'RE')",
                name=f"ck_{check_prefix}_validator_verdict",
            ),
        )
        op.create_index(f"ix_{table_name}_judgment_id", table_name, ["judgment_id"])


def downgrade() -> None:
    """Drop custom validator persistence."""
    for table_name in ("arena_submission_interactive_attempts", "submission_interactive_attempts"):
        op.drop_index(f"ix_{table_name}_judgment_id", table_name=table_name)
        op.drop_table(table_name)
    op.drop_table("arena_problem_custom_validators")
    op.drop_table("problem_custom_validators")
    bind = op.get_bind()
    _create_crash_reason_enum().drop(bind, checkfirst=True)
    _create_candidate_state_enum().drop(bind, checkfirst=True)
    _create_active_state_enum().drop(bind, checkfirst=True)
