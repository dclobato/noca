"""Track whether a requesting team has seen a clarification answer.

Revision ID: 202608200002
Revises: 202608200001
Create Date: 2026-08-20
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608200002"
down_revision: str | Sequence[str] | None = "202608200001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the answer-read timestamp without notifying for historical answers."""
    op.add_column(
        "clarifications",
        sa.Column(
            "answer_read_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Time when the requesting team first saw the clarification answer",
        ),
    )
    op.execute(sa.text("UPDATE clarifications SET answer_read_at = answered_at WHERE answered_at IS NOT NULL"))


def downgrade() -> None:
    """Drop the clarification answer-read timestamp."""
    op.drop_column("clarifications", "answer_read_at")
