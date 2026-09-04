"""Widen the worker_class column comment to name the mailer.

Revision ID: 202609010003
Revises: 202609010002
Create Date: 2026-09-01
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# `arena_worker_pause_state.worker_class` was created by 202606110003 describing
# two pausable workers. Commit 4f617be4 added the mailer as a third and updated
# the comment in `shared/db_schema/arena/arena_worker_control.py`, but wrote no
# migration for it -- so every deployed database has carried the narrower text
# ever since, and `alembic check` has reported the difference as pending work on
# every run since that release.
#
# Nothing behavioural depends on a column comment. What depends on it is
# `alembic check`: a standing difference trains readers to expect noise from it,
# which is precisely how a real pending change goes unnoticed. This closes it so
# the check is meaningful again.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202609010003"
down_revision: str | Sequence[str] | None = "202609010002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_COMMENT = "WorkerClass value (autojudge or aiassistant)."
_NEW_COMMENT = "WorkerClass value (autojudge, aiassistant or mailer)."


def upgrade() -> None:
    """Name the mailer among the pausable worker classes."""
    op.alter_column(
        "arena_worker_pause_state",
        "worker_class",
        existing_type=sa.String(32),
        existing_nullable=False,
        existing_comment=_OLD_COMMENT,
        comment=_NEW_COMMENT,
    )


def downgrade() -> None:
    """Restore the two-worker description this column was created with."""
    op.alter_column(
        "arena_worker_pause_state",
        "worker_class",
        existing_type=sa.String(32),
        existing_nullable=False,
        existing_comment=_NEW_COMMENT,
        comment=_OLD_COMMENT,
    )
