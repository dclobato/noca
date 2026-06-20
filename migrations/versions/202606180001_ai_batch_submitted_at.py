#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Add submitted_at to arena_ai_batch_jobs for stale-batch detection.

The stale-batch detector compares submitted_at against a configurable age
threshold to find batches that never reached a terminal status. Backfill
non-terminal rows that already have an OpenAI batch id using created_at as the
best available approximation.

Revision ID: 202606180001
Revises: 202606170001
Create Date: 2026-06-18 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606180001"
down_revision: str | None = "202606170001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable submitted_at column and backfill non-terminal submitted rows."""
    op.add_column(
        "arena_ai_batch_jobs",
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE arena_ai_batch_jobs
        SET submitted_at = created_at
        WHERE openai_batch_id IS NOT NULL
          AND local_status NOT IN ('completed', 'failed', 'expired', 'cancelled')
        """
    )


def downgrade() -> None:
    """Drop the submitted_at column."""
    op.drop_column("arena_ai_batch_jobs", "submitted_at")
