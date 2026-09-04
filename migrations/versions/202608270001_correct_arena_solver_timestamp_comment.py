"""Correct the arena solver timestamp column comment.

Revision ID: 202608270001
Revises: 202608250001
Create Date: 2026-08-27
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608270001"
down_revision: str | Sequence[str] | None = "202608250001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_COMMENT = "Timestamp of the first accepted submission for this (user, problem) pair."
_NEW_COMMENT = "Timestamp when the first AC judgment completed for this (user, problem) pair."


def upgrade() -> None:
    """Describe ``solved_at`` as the first-AC judgment-completion time."""
    op.alter_column(
        "arena_problem_solvers",
        "solved_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        existing_comment=_OLD_COMMENT,
        comment=_NEW_COMMENT,
    )


def downgrade() -> None:
    """Restore the former, less precise ``solved_at`` description."""
    op.alter_column(
        "arena_problem_solvers",
        "solved_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        existing_comment=_NEW_COMMENT,
        comment=_OLD_COMMENT,
    )
