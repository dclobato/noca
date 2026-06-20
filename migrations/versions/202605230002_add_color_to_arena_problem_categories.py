"""Add color to arena_problem_categories.

Revision ID: 202605230002
Revises: 202605230001
Create Date: 2026-05-23 00:02:00.000000

"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605230002"
down_revision: str | Sequence[str] | None = "202605230001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add badge color metadata to arena problem categories."""
    op.add_column(
        "arena_problem_categories",
        sa.Column(
            "color",
            sa.String(length=7),
            nullable=False,
            server_default="#6c757d",
            comment="Hex color used to render the category badge, e.g. '#6c757d'.",
        ),
    )
    op.create_check_constraint(
        "ck_arena_problem_categories_color_hex",
        "arena_problem_categories",
        "length(color) = 7 AND substr(color, 1, 1) = '#'",
    )


def downgrade() -> None:
    """Remove badge color metadata from arena problem categories."""
    op.drop_constraint(
        "ck_arena_problem_categories_color_hex",
        "arena_problem_categories",
        type_="check",
    )
    op.drop_column("arena_problem_categories", "color")
