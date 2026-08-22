"""Align interactive solution-test diagnostics with submission attempts.

Revision ID: 202608200001
Revises: 202608190001
Create Date: 2026-08-20 00:01:00.000000

"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202608200001"
down_revision: str | Sequence[str] | None = "202608190001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "solution_test_case_results"


def _verdict_enum_ref() -> postgresql.ENUM:
    return postgresql.ENUM("AC", "PE", "WA", "TLE", "MLE", "OLE", "RE", "CE", name="verdict", create_type=False)


def _crash_reason_enum_ref() -> postgresql.ENUM:
    return postgresql.ENUM(
        "SIGNAL", "STARTUP", "COMMUNICATION", "WATCHDOG", name="customvalidatorcrashreason", create_type=False
    )


def upgrade() -> None:
    """Add validator-side diagnostic columns for interactive solution-test attempts.

    These columns apply table-wide, not only to interactive rows: ordinary
    (``attempt_number IS NULL``) rows simply leave them NULL.
    """
    op.add_column(_TABLE, sa.Column("validator_exit_code", sa.Integer()))
    op.add_column(_TABLE, sa.Column("validator_signal", sa.Integer()))
    op.add_column(_TABLE, sa.Column("validator_stderr_excerpt", sa.Text()))
    op.add_column(_TABLE, sa.Column("limit_outcome", sa.String(16)))
    op.add_column(_TABLE, sa.Column("validator_verdict", _verdict_enum_ref()))
    op.add_column(_TABLE, sa.Column("crash_reason", _crash_reason_enum_ref()))

    op.create_check_constraint(
        "ck_solution_test_case_results_outcome_exclusive",
        _TABLE,
        "NOT (validator_verdict IS NOT NULL AND crash_reason IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_solution_test_case_results_limit_outcome",
        _TABLE,
        "limit_outcome IS NULL OR limit_outcome IN ('MLE', 'OLE', 'TLE')",
    )
    op.create_check_constraint(
        "ck_solution_test_case_results_validator_verdict",
        _TABLE,
        "validator_verdict IS NULL OR validator_verdict IN ('AC', 'WA', 'TLE', 'PE', 'RE')",
    )


def downgrade() -> None:
    """Drop the validator-side diagnostic columns and their constraints."""
    op.drop_constraint("ck_solution_test_case_results_validator_verdict", _TABLE, type_="check")
    op.drop_constraint("ck_solution_test_case_results_limit_outcome", _TABLE, type_="check")
    op.drop_constraint("ck_solution_test_case_results_outcome_exclusive", _TABLE, type_="check")

    op.drop_column(_TABLE, "crash_reason")
    op.drop_column(_TABLE, "validator_verdict")
    op.drop_column(_TABLE, "limit_outcome")
    op.drop_column(_TABLE, "validator_stderr_excerpt")
    op.drop_column(_TABLE, "validator_signal")
    op.drop_column(_TABLE, "validator_exit_code")
