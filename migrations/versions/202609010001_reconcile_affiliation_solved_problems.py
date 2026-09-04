#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reconcile affiliation solve totals after inserting revision 202608300002.

Some development databases reached ``202608310002`` before ``202608300002``
was inserted into the migration graph. Alembic correctly considers every new
ancestor already applied, so those databases need an idempotent descendant to
create and backfill the column. Fresh databases already have the column and
only repeat the harmless backfill.

The downgrade is intentionally a no-op because revision ``202608300002`` owns
the column's lifecycle. Dropping it here would break the schema at that earlier
revision.

Revision ID: 202609010001
Revises: 202608310002
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "202609010001"
down_revision: str | Sequence[str] | None = "202608310002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BACKFILL_SQL = (
    "UPDATE arena_affiliations AS affiliation "
    "SET solved_problems = COALESCE(("
    "SELECT SUM(COALESCE(arena_users.solved_problems, 0)) "
    "FROM arena_users "
    "WHERE arena_users.affiliation_id = affiliation.id "
    "AND arena_users.ranking_visible IS TRUE "
    "AND arena_users.user_rating IS NOT NULL"
    "), 0)"
)


def upgrade() -> None:
    """Create the solve-total column when missing and refresh every total."""
    op.execute("ALTER TABLE arena_affiliations ADD COLUMN IF NOT EXISTS solved_problems INTEGER NOT NULL DEFAULT 0")
    op.execute(
        "COMMENT ON COLUMN arena_affiliations.solved_problems IS "
        "'Total counted solves by ranking-visible affiliation members'"
    )
    op.execute(_BACKFILL_SQL)


def downgrade() -> None:
    """Leave the column owned by revision 202608300002 unchanged."""
