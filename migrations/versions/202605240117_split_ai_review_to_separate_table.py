#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Split AI review fields from arena_submissions into a dedicated table.

Moves the three AI review columns added by revision 202605240115 out of
``arena_submissions`` and into the new sparse ``arena_submission_ai_reviews``
table (submission_id is the primary key, enforcing the 1:1 relationship at the
DB level).

Motivation: most submissions will never receive an AI review, so keeping these
nullable columns on every row wastes storage and adds I/O cost to every
arena_submissions SELECT.

Data migration: any rows in ``arena_submissions`` where ``ai_response IS NOT NULL``
are copied into ``arena_submission_ai_reviews`` before the columns are dropped.

Revision ID: 202605240117
Revises: 202605240116
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605240117"
down_revision: str | Sequence[str] | None = "202605240116"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create arena_submission_ai_reviews, migrate data, drop old columns."""
    op.create_table(
        "arena_submission_ai_reviews",
        sa.Column(
            "submission_id",
            sa.String(36),
            sa.ForeignKey("arena_submissions.id", ondelete="CASCADE"),
            primary_key=True,
            comment=(
                "1:1 FK to arena_submissions; submission_id is the primary key "
                "(at most one review per submission)."
            ),
        ),
        sa.Column(
            "ai_response",
            sa.Text(),
            nullable=False,
            comment="AI provider response text for this submission",
        ),
        sa.Column(
            "ai_response_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Timestamp when the AI provider response was stored",
        ),
        sa.Column(
            "_ai_review_cost",
            sa.Integer(),
            nullable=True,
            comment=(
                "AI review cost stored as integer micros (value * 1_000_000); "
                "access via ai_review_cost property"
            ),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Migrate any existing AI review data from the old columns.
    op.execute(
        sa.text(
            """
            INSERT INTO arena_submission_ai_reviews
                (submission_id, ai_response, ai_response_at, _ai_review_cost, created_at)
            SELECT
                id,
                ai_response,
                ai_response_at,
                _ai_review_cost,
                now()
            FROM arena_submissions
            WHERE ai_response IS NOT NULL
              AND ai_response_at IS NOT NULL
            ON CONFLICT (submission_id) DO NOTHING
            """
        )
    )

    op.drop_column("arena_submissions", "_ai_review_cost")
    op.drop_column("arena_submissions", "ai_response_at")
    op.drop_column("arena_submissions", "ai_response")


def downgrade() -> None:
    """Re-add old columns to arena_submissions, restore data, drop new table."""
    op.add_column(
        "arena_submissions",
        sa.Column(
            "ai_response",
            sa.Text(),
            nullable=True,
            comment="AI provider response text for this submission",
        ),
    )
    op.add_column(
        "arena_submissions",
        sa.Column(
            "ai_response_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp when the AI provider response was stored",
        ),
    )
    op.add_column(
        "arena_submissions",
        sa.Column(
            "_ai_review_cost",
            sa.Integer(),
            nullable=True,
            comment=(
                "AI review cost stored as integer micros (value * 1_000_000); "
                "access via ai_review_cost property"
            ),
        ),
    )

    # Restore review data into the old columns before dropping the new table.
    op.execute(
        sa.text(
            """
            UPDATE arena_submissions s
            SET
                ai_response      = r.ai_response,
                ai_response_at   = r.ai_response_at,
                _ai_review_cost  = r._ai_review_cost
            FROM arena_submission_ai_reviews r
            WHERE s.id = r.submission_id
            """
        )
    )

    op.drop_table("arena_submission_ai_reviews")
