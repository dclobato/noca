#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add Arena AI review fields to arena_users and arena_submissions.

Adds:
  - arena_users._ai_api_key: encrypted AI provider API key per user.
  - arena_submissions.submit_to_ai: flag to request AI feedback on a submission.
  - arena_submissions.ai_response: text response from the AI provider.
  - arena_submissions.ai_response_at: timestamp when the AI response was stored.
  - arena_submissions._ai_review_cost: AI review cost as integer micros (value * 1_000_000).

Revision ID: 202605240115
Revises: 202605240114
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605240115"
down_revision: str | Sequence[str] | None = "202605240114"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add AI review columns to arena_users and arena_submissions."""
    op.add_column(
        "arena_users",
        sa.Column(
            "_ai_api_key",
            sa.String(500),
            nullable=True,
            comment="Encrypted AI provider API key for AI-assisted code submission",
        ),
    )

    op.add_column(
        "arena_submissions",
        sa.Column(
            "submit_to_ai",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="When True, the submission should be forwarded to the configured AI provider for feedback",
        ),
    )
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
            comment="AI review cost stored as integer micros (value * 1_000_000); access via ai_review_cost property",
        ),
    )


def downgrade() -> None:
    """Remove AI review columns from arena_users and arena_submissions."""
    op.drop_column("arena_submissions", "_ai_review_cost")
    op.drop_column("arena_submissions", "ai_response_at")
    op.drop_column("arena_submissions", "ai_response")
    op.drop_column("arena_submissions", "submit_to_ai")
    op.drop_column("arena_users", "_ai_api_key")
