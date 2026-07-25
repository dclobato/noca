"""shared: Allow general (problem-less) contest clarifications.

Revision ID: 202607240001
Revises: 202607180003
Create Date: 2026-07-24 00:01:00.000000

"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607240001"
down_revision: str | Sequence[str] | None = "202607180003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMENT = (
    "Problem this clarification is about. NULL for general, contest-wide clarifications. "
    "RESTRICT on delete to prevent orphaned clarifications."
)


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "clarifications",
        "problem_id",
        existing_type=sa.String(length=36),
        nullable=True,
        comment=_COMMENT,
        existing_comment=None,
    )


def downgrade() -> None:
    """Downgrade schema."""
    general_count = op.get_bind().execute(
        sa.text("SELECT count(*) FROM clarifications WHERE problem_id IS NULL")
    ).scalar_one()
    if general_count:
        raise RuntimeError(
            f"Cannot downgrade: {general_count} general clarification(s) have no problem. "
            "Attach or remove them before downgrading."
        )
    op.alter_column(
        "clarifications",
        "problem_id",
        existing_type=sa.String(length=36),
        nullable=False,
        comment=None,
        existing_comment=_COMMENT,
    )
