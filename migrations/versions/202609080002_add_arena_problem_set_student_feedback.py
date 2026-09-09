#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""add arena_problem_set_student_feedback

Each student/set pair retains only one small feedback row, updated when the
teacher edits it and deleted only when the feedback is withdrawn or either
parent is removed. This is low-churn reference-style data, so the server-wide
autovacuum defaults are appropriate; no per-table storage parameters are needed.

Revision ID: 202609080002
Revises: 202609080001
Create Date: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202609080002"
down_revision: str | Sequence[str] | None = "202609080001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the one-feedback-per-student-and-problem-set table."""
    op.create_table(
        "arena_problem_set_student_feedback",
        sa.Column(
            "problem_set_id",
            sa.String(length=36),
            nullable=False,
            comment="Problem set that the feedback covers.",
        ),
        sa.Column(
            "student_id",
            sa.String(length=36),
            nullable=False,
            comment="Student who receives the feedback.",
        ),
        sa.Column(
            "teacher_id",
            sa.String(length=36),
            nullable=True,
            comment="Teacher or admin who most recently wrote the feedback.",
        ),
        sa.Column(
            "feedback_text",
            sa.Text(),
            nullable=False,
            comment="Markdown feedback for the student's work across the problem set.",
        ),
        sa.Column(
            "feedback_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="When the feedback was last saved.",
        ),
        sa.ForeignKeyConstraint(["problem_set_id"], ["arena_problem_sets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["arena_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["teacher_id"], ["arena_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("problem_set_id", "student_id"),
    )


def downgrade() -> None:
    """Drop the problem-set student feedback table."""
    op.drop_table("arena_problem_set_student_feedback")
