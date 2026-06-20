"""Squash image caption, preferred language, and rating scale changes.

Revision ID: 202605240113
Revises: 202605230003
Create Date: 2026-05-24 01:13:00.000000

"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605240113"
down_revision: str = "202605230003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Apply the final schema changes introduced by the last four revisions."""
    op.add_column(
        "arena_problems",
        sa.Column(
            "problem_image_caption",
            sa.String(length=512),
            nullable=True,
            comment="Optional caption displayed below the problem image.",
        ),
    )
    op.add_column(
        "arena_users",
        sa.Column(
            "preferred_language_id",
            sa.String(length=64),
            nullable=True,
            comment="User's preferred programming language for submissions",
        ),
    )
    op.create_index(
        "ix_arena_users_preferred_language_id",
        "arena_users",
        ["preferred_language_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_arena_users_preferred_language_id",
        "arena_users",
        "languages",
        ["preferred_language_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE arena_users
        SET preferred_language_id = sub.lang_id
        FROM (
            SELECT id AS lang_id
            FROM languages
            WHERE active = true
            ORDER BY name
            LIMIT 1
        ) AS sub
        WHERE preferred_language_id IS NULL
        """,
    )
    op.drop_constraint("ck_arena_problem_ratings_rating_range", "arena_problem_ratings", type_="check")
    op.execute("UPDATE arena_problem_ratings SET rating = rating * 10")
    op.execute("UPDATE arena_problem_rating_history SET rating = rating * 10")
    op.create_check_constraint(
        "ck_arena_problem_ratings_rating_range",
        "arena_problem_ratings",
        "rating BETWEEN 0 AND 100",
    )
    op.alter_column(
        "arena_problem_ratings",
        "rating",
        existing_type=sa.Integer(),
        server_default="50",
        existing_nullable=False,
    )


def downgrade() -> None:
    """Revert the final schema changes introduced by the squashed revision."""
    op.alter_column(
        "arena_problem_ratings",
        "rating",
        existing_type=sa.Integer(),
        server_default="5",
        existing_nullable=False,
    )
    op.drop_constraint("ck_arena_problem_ratings_rating_range", "arena_problem_ratings", type_="check")
    op.execute("UPDATE arena_problem_ratings SET rating = ROUND(rating / 10.0)")
    op.execute("UPDATE arena_problem_rating_history SET rating = ROUND(rating / 10.0)")
    op.create_check_constraint(
        "ck_arena_problem_ratings_rating_range",
        "arena_problem_ratings",
        "rating BETWEEN 0 AND 10",
    )
    op.drop_constraint("fk_arena_users_preferred_language_id", "arena_users", type_="foreignkey")
    op.drop_index("ix_arena_users_preferred_language_id", table_name="arena_users")
    op.drop_column("arena_users", "preferred_language_id")
    op.drop_column("arena_problems", "problem_image_caption")
