#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add the acquisition-time columns to ``tasks`` and ``clarifications``.

Both tables lock a row to one handler through a Valkey lock, and until now the
acquisition instant lived only on that lock. A finished task or answered
clarification releases its lock as its last step, so the moment "how long did
this take to handle" became answerable from a real end time, the start time it
needed was already gone -- the contest reports and task/clarification list
pages could show a live-ticking service time for a row in progress, but never
a final one for a row that had finished.

``acquired_at`` / ``acquired_timestamp_seconds`` persist that start time on the
row itself, mirroring the existing ``created_*`` / ``finished_*`` (or
``answered_*``) pairs on each table. Each acquire overwrites the pair, so a row
that was acquired, released, and re-acquired by someone else reports the last
handler's service time, not the total time the row spent unclaimed between
handlers -- the same standard already applied to ``staff_id`` / ``judge_id``.
Release does not clear them: an unfinished row's service time is read from the
live lock, never from these columns, so a stale value sitting between releases
is inert.

Both tables already take one write per acquire under their existing traffic
(one row update per handle, same order of magnitude as the existing
``finished_at`` / ``answered_at`` write); this adds one more update of the same
row, not a new write pattern, and neither table needs per-table autovacuum
tuning as a result.

Revision ID: 202609060001
Revises: 202609050001
Create Date: 2026-09-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202609060001"
down_revision: str | Sequence[str] | None = "202609050001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Byte-identical to the comments in shared/db_schema/contest.py and
# shared/db_schema/clarification.py, so the metadata and the database agree
# and autogenerate reports no drift.
_TASK_ACQUIRED_AT_COMMENT = (
    "Time when the task was last acquired by its handler. Reset on every acquire; not cleared "
    "on release, since an unfinished task's service time is never read from it."
)
_TASK_ACQUIRED_TIMESTAMP_COMMENT = "Seconds since contest start when the task was last acquired."
_CLARIFICATION_ACQUIRED_AT_COMMENT = (
    "Time when the clarification was last acquired by its handler. Reset on every acquire; not "
    "cleared on release, since an unanswered clarification's service time is never read from it."
)
_CLARIFICATION_ACQUIRED_TIMESTAMP_COMMENT = "Seconds since contest start when the clarification was last acquired."

_COLUMN_NAMES: tuple[str, ...] = ("acquired_at", "acquired_timestamp_seconds")


def upgrade() -> None:
    """Add the acquisition-time columns to ``tasks`` and ``clarifications``."""
    op.add_column(
        "tasks",
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=_TASK_ACQUIRED_AT_COMMENT,
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "acquired_timestamp_seconds",
            sa.Integer(),
            nullable=True,
            comment=_TASK_ACQUIRED_TIMESTAMP_COMMENT,
        ),
    )
    op.add_column(
        "clarifications",
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=_CLARIFICATION_ACQUIRED_AT_COMMENT,
        ),
    )
    op.add_column(
        "clarifications",
        sa.Column(
            "acquired_timestamp_seconds",
            sa.Integer(),
            nullable=True,
            comment=_CLARIFICATION_ACQUIRED_TIMESTAMP_COMMENT,
        ),
    )


def downgrade() -> None:
    """Remove the acquisition-time columns from ``tasks`` and ``clarifications``."""
    for name in reversed(_COLUMN_NAMES):
        op.drop_column("clarifications", name)
    for name in reversed(_COLUMN_NAMES):
        op.drop_column("tasks", name)
