#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""require an awarding submission on every arena_user_badges row

A badge is a claim about a piece of work. If it cannot name the submission that
earned it, it is not a badge -- it is an assertion with nothing behind it. This
revision makes the database say so: ``submission_id`` becomes ``NOT NULL`` and
its foreign key becomes ``ON DELETE CASCADE``, so a deleted submission takes the
badges that named it rather than leaving them pointing at nothing.

The rows that name no submission are deleted first. They are of two kinds and
both are handled by the rating worker rather than by this migration: a row whose
badge is still earned is re-awarded, with an anchor, by the next full reconcile;
a row whose criterion no longer holds is a revocation that should already have
happened. On the production database this deletes 8 rows, 3 of which (CLEAN_CODE)
come straight back anchored.

An index on ``submission_id`` comes with the cascade. PostgreSQL does not index
the referencing side of a foreign key, and deleting a problem deletes all of its
submissions, so without one every such delete scans the badge table once per
submission.

Autovacuum tuning comes with it too, and for the same reason: this change turns
``arena_user_badges`` from an append-only ledger into a table the full-reconcile
pass rewrites -- inserting, re-anchoring and revoking -- every cycle. It is
delete/update churn rather than an insert-heavy log, so it takes the log profile
without the insert-driven trigger; badges are never appended in bulk, so
``autovacuum_vacuum_insert_scale_factor`` would buy nothing here. The scale
factors matter little at today's few hundred rows and matter a great deal at tens
of thousands, which is why the absolute thresholds are lowered as well.

The reconciliation itself only writes what actually changed -- a pass over an
unchanged database issues no statement against this table -- so the tuning is
sized for real movement in the badge set, not for a per-pass rewrite.

Revision ID: 202609100002
Revises: 202609100001
Create Date: 2026-09-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202609100002"
down_revision: str | Sequence[str] | None = "202609100001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK = "fk_arena_user_badges_submission_id"
_INDEX = "ix_arena_user_badges_submission_id"
_TABLE = "arena_user_badges"

# Delete/update churn from the full-reconcile pass. Matches the log profile in
# 202607180003 minus the insert-driven trigger, which an append-heavy log needs
# and a reconciled set does not.
_AUTOVACUUM = {
    "autovacuum_vacuum_scale_factor": "0.05",
    "autovacuum_vacuum_threshold": "100",
    "autovacuum_analyze_scale_factor": "0.05",
    "autovacuum_analyze_threshold": "100",
}


def upgrade() -> None:
    """Make the awarding submission mandatory and cascade its deletion."""
    op.execute(f"DELETE FROM {_TABLE} WHERE submission_id IS NULL")
    op.drop_constraint(_FK, _TABLE, type_="foreignkey")
    op.alter_column(_TABLE, "submission_id", existing_type=sa.String(length=36), nullable=False)
    op.create_foreign_key(
        _FK,
        _TABLE,
        "arena_submissions",
        ["submission_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(_INDEX, _TABLE, ["submission_id"])
    params = ", ".join(f"{key} = {value}" for key, value in _AUTOVACUUM.items())
    op.execute(f"ALTER TABLE {_TABLE} SET ({params})")


def downgrade() -> None:
    """Allow an unanchored badge again and stop deleting badges with submissions."""
    op.execute(f"ALTER TABLE {_TABLE} RESET ({', '.join(sorted(_AUTOVACUUM))})")
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_constraint(_FK, _TABLE, type_="foreignkey")
    op.alter_column(_TABLE, "submission_id", existing_type=sa.String(length=36), nullable=True)
    op.create_foreign_key(
        _FK,
        _TABLE,
        "arena_submissions",
        ["submission_id"],
        ["id"],
        ondelete="SET NULL",
    )
