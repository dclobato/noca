#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Arena problem sets: schedule, problem junction, submission link, snapshots

Revision ID: 202606040004
Revises: 202606040003
Create Date: 2026-06-04 00:04:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606040004"
down_revision: Union[str, Sequence[str], None] = "202606040003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "arena_problem_sets",
        sa.Column(
            "starts_on",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Startline: submissions are accepted from this moment on (date/time).",
        ),
    )
    op.add_column(
        "arena_problem_sets",
        sa.Column(
            "deadline",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Deadline: submissions are accepted up to this moment (date/time).",
        ),
    )

    op.create_table(
        "arena_problem_set_problems",
        sa.Column(
            "problem_set_id",
            sa.String(36),
            sa.ForeignKey("arena_problem_sets.id", ondelete="CASCADE"),
            primary_key=True,
            comment="FK to arena_problem_sets.",
        ),
        sa.Column(
            "problem_id",
            sa.String(36),
            sa.ForeignKey("arena_problems.id", ondelete="CASCADE"),
            primary_key=True,
            comment="FK to arena_problems.",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_arena_problem_set_problems_problem",
        "arena_problem_set_problems",
        ["problem_id"],
    )

    op.add_column(
        "arena_submissions",
        sa.Column(
            "problem_set_id",
            sa.String(36),
            sa.ForeignKey("arena_problem_sets.id", ondelete="SET NULL"),
            nullable=True,
            comment=(
                "When set, the submission is tied to this problem set and visible to the "
                "set's teacher. NULL means the submission is private to the submitter."
            ),
        ),
    )
    op.create_index(
        "ix_arena_submissions_problem_set_id",
        "arena_submissions",
        ["problem_set_id"],
    )

    op.create_table(
        "arena_problem_set_user_snapshots",
        sa.Column(
            "problem_set_id",
            sa.String(36),
            sa.ForeignKey("arena_problem_sets.id", ondelete="CASCADE"),
            primary_key=True,
            comment="FK to arena_problem_sets.",
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("arena_users.id", ondelete="CASCADE"),
            primary_key=True,
            comment="Arena user who submitted anything to the set.",
        ),
        sa.Column(
            "total_rating",
            sa.Integer(),
            nullable=False,
            comment="Sum of current ratings of the problems this user got AC on (0 if none).",
        ),
        sa.Column(
            "snapshot_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="When the snapshot was taken.",
        ),
    )

    op.create_table(
        "arena_problem_set_problem_snapshots",
        sa.Column(
            "problem_set_id",
            sa.String(36),
            sa.ForeignKey("arena_problem_sets.id", ondelete="CASCADE"),
            primary_key=True,
            comment="FK to arena_problem_sets.",
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("arena_users.id", ondelete="CASCADE"),
            primary_key=True,
            comment="Arena user who got AC on the problem.",
        ),
        sa.Column(
            "problem_id",
            sa.String(36),
            sa.ForeignKey("arena_problems.id", ondelete="CASCADE"),
            primary_key=True,
            comment="FK to arena_problems; a problem in the set the user got AC on.",
        ),
        sa.Column(
            "rating",
            sa.Integer(),
            nullable=False,
            comment="The problem's rating captured at snapshot time.",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("arena_problem_set_problem_snapshots")
    op.drop_table("arena_problem_set_user_snapshots")
    op.drop_index("ix_arena_submissions_problem_set_id", table_name="arena_submissions")
    op.drop_column("arena_submissions", "problem_set_id")
    op.drop_index(
        "ix_arena_problem_set_problems_problem",
        table_name="arena_problem_set_problems",
    )
    op.drop_table("arena_problem_set_problems")
    op.drop_column("arena_problem_sets", "deadline")
    op.drop_column("arena_problem_sets", "starts_on")
