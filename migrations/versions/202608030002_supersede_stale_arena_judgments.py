#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Supersede stale Arena judgments left active by problem-wide rejudges.

``arena.services.admin_problem_service.build_rejudge_jobs`` (the admin
"rejudge every submission of this problem" action) used to insert a fresh
``QUEUED`` judgment per submission without marking the previous one
``SUPERSEDED``, unlike the single-submission rejudge. Affected submissions
therefore carry more than one non-superseded judgment, which fans out every
query that outer-joins active judgments: the teacher report showed one
submission once per judgment, inflating attempt counts.

This is a data repair, not a schema change. For each submission with more than
one non-superseded judgment it keeps the newest (by ``created_at``, ties broken
by ``id`` so the choice is deterministic and matches what the read helpers
select) and marks the rest ``SUPERSEDED``. Verdict-picking code already reads
the newest judgment, so the retained row is the one users were already seeing;
only the redundant rows change.

Revision ID: 202608030002
Revises: 202608030001
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "202608030002"
down_revision: str | Sequence[str] | None = "202608030001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SUPERSEDE_STALE = """
UPDATE arena_submission_judgments AS j
SET status = 'SUPERSEDED'
WHERE j.status <> 'SUPERSEDED'
  AND EXISTS (
      SELECT 1
      FROM arena_submission_judgments AS newer
      WHERE newer.submission_id = j.submission_id
        AND newer.status <> 'SUPERSEDED'
        AND (newer.created_at, newer.id) > (j.created_at, j.id)
  )
"""


def upgrade() -> None:
    """Mark every non-newest active Arena judgment as superseded."""
    op.execute(_SUPERSEDE_STALE)


def downgrade() -> None:
    """No-op: the pre-repair statuses cannot be reconstructed.

    A ``SUPERSEDED`` row written by this migration is indistinguishable from one
    written by a legitimate rejudge, so reverting would have to guess which rows
    to reactivate and would recreate the fan-out bug for the ones it guessed
    wrong. Leaving the repaired data in place is the safe direction.
    """
