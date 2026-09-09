#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""add arena_user_badges.submission_id

``arena_user_badges`` recorded *that* a user earned a badge and *when*, but
nothing about *what* earned it. Every rule already has the awarding submission
in hand when it calls ``award_badge()``; this column stops discarding it.

Nullable with ``ON DELETE SET NULL`` rather than ``NOT NULL``/``CASCADE``:
deleting a submission must not delete the badge it earned, CLEAN_CODE records a
rank and has no single awarding submission at all, and an existing row whose
anchor is no longer reachable under today's data stays NULL.

The column ships NULL for every existing row and needs no data migration. The
rating worker's periodic full-reconcile pass re-derives every badge from all
Accepted history, and ``award_badge()`` fills a NULL anchor on a badge the user
already holds without ever rewriting one that is set, so the existing rows
acquire their anchors within one reconcile interval on their own.

That backfill is best-effort by nature: it yields the earliest submission that
would award the badge under *today's* data, which diverges from the historical
one after a rejudge, after a problem set is deleted (``problem_set_id`` is
``ON DELETE SET NULL``, so FIRST_TO_HAND_IN, ALMOST_LATE and FULL_CLEAR
correctly stay NULL), or after set membership or deadlines move. ``awarded_at``
is never rewritten, so a filled row may point at a submission whose timestamp
disagrees with its award timestamp; that skew is accepted.

``arena_user_badges`` is append-only and holds one row per user per badge, so it
stays on the server-wide autovacuum defaults; no per-table storage parameters
are needed. No index is added on the new column either -- it is read by joining
from a badge row, never searched by submission, and the table is small enough
for the ``SET NULL`` scan that a submission delete would trigger.

Revision ID: 202609100001
Revises: 202609090001
Create Date: 2026-09-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202609100001"
down_revision: str | Sequence[str] | None = "202609090001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable awarding-submission foreign key."""
    op.add_column("arena_user_badges", sa.Column("submission_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_arena_user_badges_submission_id",
        "arena_user_badges",
        "arena_submissions",
        ["submission_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Drop the awarding-submission foreign key and column."""
    op.drop_constraint("fk_arena_user_badges_submission_id", "arena_user_badges", type_="foreignkey")
    op.drop_column("arena_user_badges", "submission_id")
