#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add arena_problems.expected_difficulty.

The author's declared expected difficulty on the internal ``[1, 100]`` rating
scale. It seeds the solve-rate prior of the difficulty rating so a fresh problem
starts where its author expects rather than at the flat centre; evidence then
overrides it. Nullable, because the existing catalogue has no estimate and
forcing one at edit time would produce guesses worse than the flat prior.

Low-churn reference data; the server-wide autovacuum defaults apply.

Revision ID: 202608300001
Revises: 202608270001
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608300001"
down_revision: str | Sequence[str] | None = "202608270001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMN_COMMENT = "Author-declared expected difficulty on the internal 1-100 scale; NULL when not estimated."
_CHECK_NAME = "ck_arena_problems_expected_difficulty_range"


def upgrade() -> None:
    """Add the nullable expected_difficulty column and its range check."""
    op.add_column(
        "arena_problems",
        sa.Column("expected_difficulty", sa.Integer(), nullable=True, comment=_COLUMN_COMMENT),
    )
    op.create_check_constraint(
        _CHECK_NAME,
        "arena_problems",
        "expected_difficulty IS NULL OR expected_difficulty BETWEEN 1 AND 100",
    )


def downgrade() -> None:
    """Drop the range check and the expected_difficulty column."""
    op.drop_constraint(_CHECK_NAME, "arena_problems", type_="check")
    op.drop_column("arena_problems", "expected_difficulty")
