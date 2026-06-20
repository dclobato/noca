#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Windowed batch staging: make openai_batch_id nullable and non-unique.

Platform-key AI review jobs are now staged in arena_ai_batch_jobs with
local_status='staged' before the batch flusher bundles them into one
multi-item OpenAI batch. Multiple rows sharing the same openai_batch_id
requires removing the unique constraint and making the column nullable.

Revision ID: 202606170001
Revises: 202606140003
Create Date: 2026-06-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606170001"
down_revision: str | None = "202606140003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove unique constraint on openai_batch_id, make it nullable, add plain index."""
    op.drop_constraint("arena_ai_batch_jobs_openai_batch_id_key", "arena_ai_batch_jobs", type_="unique")
    op.alter_column("arena_ai_batch_jobs", "openai_batch_id", nullable=True)
    op.create_index(
        "ix_arena_ai_batch_jobs_openai_batch_id",
        "arena_ai_batch_jobs",
        ["openai_batch_id"],
    )


def downgrade() -> None:
    """Restore unique constraint and NOT NULL on openai_batch_id."""
    op.drop_index("ix_arena_ai_batch_jobs_openai_batch_id", table_name="arena_ai_batch_jobs")
    op.alter_column("arena_ai_batch_jobs", "openai_batch_id", nullable=False)
    op.create_unique_constraint(
        "arena_ai_batch_jobs_openai_batch_id_key",
        "arena_ai_batch_jobs",
        ["openai_batch_id"],
    )
