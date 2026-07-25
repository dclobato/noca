"""shared: Add attempt-scoped claim tokens to every worker-owned run.

Revision ID: 202607250001
Revises: 202607240002
Create Date: 2026-07-25 00:01:00.000000

"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607250001"
down_revision: str | Sequence[str] | None = "202607240002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "submission_judgments",
    "arena_submission_judgments",
    "profiling_runs",
    "solution_test_runs",
)

_COMMENT = (
    "Attempt-scoped claim stamped at dispatch. Identifies one attempt at this run, "
    "not one worker: two attempts may share a worker_id (same host and process). "
    "Every later write of that attempt is fenced on it, so an attempt whose claim "
    "was taken over by a reaper requeue cannot mutate the run."
)


def upgrade() -> None:
    """Upgrade schema.

    The column is nullable with no backfill: rows judged before this revision
    have no attempt to identify, and a NULL claim is treated as unclaimed.
    """
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("attempt_token", sa.String(length=72), nullable=True, comment=_COMMENT),
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table in _TABLES:
        op.drop_column(table, "attempt_token")
