#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Arena precomputed user statistics table

Stores the verdict and language distribution for every Arena user that has
at least one judged submission. Read by the public profile page so it can
render the doughnut charts without running heavy aggregate queries on every
request. Snapshots are produced periodically by the rating worker.

Revision ID: 202606220005
Revises: 202606220004
Create Date: 2026-06-22
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606220005"
down_revision: str | Sequence[str] | None = "202606220004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "arena_user_statistics",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("arena_users.id", ondelete="CASCADE"),
            primary_key=True,
            comment="FK to arena_users.",
        ),
        sa.Column(
            "data",
            sa.JSON(),
            nullable=False,
            comment="Precomputed statistics payload: verdicts, languages.",
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("arena_user_statistics")