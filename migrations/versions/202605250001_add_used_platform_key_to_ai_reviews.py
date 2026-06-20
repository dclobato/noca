#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add used_platform_key column to arena_submission_ai_reviews.

Records whether each AI review was performed using the platform API key (True)
or the user's own API key (False). This allows tracking per-review key usage
independently of cost (which may be None when token usage data is unavailable).

Backfill strategy: existing rows are set to True when ``_ai_review_cost IS NOT
NULL`` (cost was tracked, meaning the platform key was in use at the time) and
False otherwise (user key, or a row created before cost tracking existed).

Revision ID: 202605250001
Revises: 202605240117
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605250001"
down_revision: str | Sequence[str] | None = "202605240117"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add used_platform_key column and backfill from existing cost data."""
    op.add_column(
        "arena_submission_ai_reviews",
        sa.Column(
            "used_platform_key",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment=(
                "True when the platform API key was used for this review; "
                "False when the user's own API key was used"
            ),
        ),
    )

    # Backfill: rows that already have a cost recorded were generated with the
    # platform key (cost was only tracked for platform-key calls before this
    # migration); all other existing rows default to False (user key).
    op.execute(
        sa.text(
            """
            UPDATE arena_submission_ai_reviews
            SET used_platform_key = TRUE
            WHERE _ai_review_cost IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    """Remove used_platform_key column."""
    op.drop_column("arena_submission_ai_reviews", "used_platform_key")
