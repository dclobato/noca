#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add precomputed solved-problem totals to Arena affiliations.

The affiliation rating cycle updates this low-churn summary column from each
ranking-visible member's precomputed user total. Existing rows are backfilled
with the same semantics so the ranking is accurate before the next worker run.
Server-wide autovacuum defaults are sufficient for the affiliation catalog.

Revision ID: 202608300002
Revises: 202608300001
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608300002"
down_revision: str | Sequence[str] | None = "202608300001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMN_COMMENT = "Total counted solves by ranking-visible affiliation members"


def upgrade() -> None:
    """Add and backfill the affiliation solved-problem total."""
    op.add_column(
        "arena_affiliations",
        sa.Column(
            "solved_problems",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment=_COLUMN_COMMENT,
        ),
    )
    op.execute(
        "UPDATE arena_affiliations AS affiliation "
        "SET solved_problems = COALESCE(("
        "SELECT SUM(COALESCE(arena_users.solved_problems, 0)) "
        "FROM arena_users "
        "WHERE arena_users.affiliation_id = affiliation.id "
        "AND arena_users.ranking_visible IS TRUE "
        "AND arena_users.user_rating IS NOT NULL"
        "), 0)"
    )


def downgrade() -> None:
    """Remove the affiliation solved-problem total."""
    op.drop_column("arena_affiliations", "solved_problems")
