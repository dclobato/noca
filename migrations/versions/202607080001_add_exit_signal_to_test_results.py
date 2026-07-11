#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add exit_signal to submission and arena submission test results.

Revision ID: 202607080001
Revises: 202607030001
Create Date: 2026-07-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607080001"
down_revision: str | Sequence[str] | None = "202607030001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMENT = "Fatal signal number reported by isolate (exitsig), when the process was signal-killed."


def upgrade() -> None:
    """Add the nullable exit_signal column to both test-result tables."""
    op.add_column(
        "submission_test_results",
        sa.Column("exit_signal", sa.Integer(), nullable=True, comment=_COMMENT),
    )
    op.add_column(
        "arena_submission_test_results",
        sa.Column("exit_signal", sa.Integer(), nullable=True, comment=_COMMENT),
    )


def downgrade() -> None:
    """Drop the exit_signal column from both test-result tables."""
    op.drop_column("arena_submission_test_results", "exit_signal")
    op.drop_column("submission_test_results", "exit_signal")
