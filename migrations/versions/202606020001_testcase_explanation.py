#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Add explanation column to test_cases and arena_test_cases

Revision ID: 202606020001
Revises: 202606010002
Create Date: 2026-06-02 00:01:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606020001"
down_revision: Union[str, Sequence[str], None] = "202606010002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EXPLANATION_COMMENT = "Optional author note explaining why this test case has its expected output."


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "test_cases",
        sa.Column("explanation", sa.String(1024), nullable=True, comment=_EXPLANATION_COMMENT),
    )
    op.add_column(
        "arena_test_cases",
        sa.Column("explanation", sa.String(1024), nullable=True, comment=_EXPLANATION_COMMENT),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("arena_test_cases", "explanation")
    op.drop_column("test_cases", "explanation")
