#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""add arena_submission_teacher_feedback

Revision ID: 202606110001
Revises: 202606100001
Create Date: 2026-06-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606110001"
down_revision: str | Sequence[str] | None = "202606100001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "arena_submission_teacher_feedback",
        sa.Column(
            "submission_id",
            sa.String(length=36),
            nullable=False,
            comment=(
                "1:1 FK to arena_submissions; submission_id is the primary key "
                "(at most one feedback per submission)."
            ),
        ),
        sa.Column(
            "teacher_id",
            sa.String(length=36),
            nullable=True,
            comment=(
                "Teacher (ARENA_JUDGE/ARENA_ADMIN) who authored the feedback; "
                "SET NULL if that account is removed."
            ),
        ),
        sa.Column(
            "feedback_text",
            sa.Text(),
            nullable=False,
            comment="Free-text feedback the teacher left on the student's submission.",
        ),
        sa.Column(
            "feedback_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Timestamp the feedback was last written (refreshed on every edit).",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["arena_submissions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["arena_users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("submission_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("arena_submission_teacher_feedback")
