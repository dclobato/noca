"""shared: Record which test case parametrized each interactive validator attempt.

Revision ID: 202607140001
Revises: 202607130001
Create Date: 2026-07-14 00:01:00.000000

"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607140001"
down_revision: str | Sequence[str] | None = "202607130001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    ("submission_interactive_attempts", "ck_submission_interactive_test_case_ordinal"),
    ("arena_submission_interactive_attempts", "ck_arena_interactive_test_case_ordinal"),
)


def upgrade() -> None:
    """Upgrade schema."""
    for table_name, constraint_name in _TABLES:
        op.add_column(
            table_name,
            sa.Column(
                "test_case_ordinal",
                sa.Integer(),
                nullable=True,
                comment="1-based ordinal of the test case that parametrized this attempt; NULL for legacy rows.",
            ),
        )
        op.create_check_constraint(
            constraint_name,
            table_name,
            "test_case_ordinal IS NULL OR test_case_ordinal >= 1",
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table_name, constraint_name in _TABLES:
        op.drop_constraint(constraint_name, table_name, type_="check")
        op.drop_column(table_name, "test_case_ordinal")
