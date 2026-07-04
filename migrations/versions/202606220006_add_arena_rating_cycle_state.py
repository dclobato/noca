#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add the singleton arena_rating_cycle_state snapshot table.

Creates the singleton ``arena_rating_cycle_state`` table holding the
catalogue-wide problem-difficulty histogram snapshot (``data``) and the timestamp
of the cycle that produced it (``computed_at``), written by the rating worker at
the end of every ``rate_all_problems()`` run and read by the Arena rating help
page.

Revision ID: 202606220006
Revises: 202606220005
Create Date: 2026-06-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202606220006"
down_revision: str | Sequence[str] | None = "202606220005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create arena_rating_cycle_state and seed its singleton row."""
    op.create_table(
        "arena_rating_cycle_state",
        sa.Column("id", sa.String(length=16), nullable=False, comment="Fixed singleton key; always 'singleton'."),
        sa.Column(
            "data",
            sa.JSON(),
            nullable=True,
            comment="Latest problem-difficulty histogram snapshot payload (JSON).",
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp of the difficulty cycle that produced the snapshot.",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 'singleton'", name="ck_arena_rating_cycle_state_singleton"),
    )
    op.execute("INSERT INTO arena_rating_cycle_state (id) VALUES ('singleton')")


def downgrade() -> None:
    """Drop arena_rating_cycle_state."""
    op.drop_table("arena_rating_cycle_state")
