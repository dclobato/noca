#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Widen test-case explanation columns from String(1024) to Text.

Removes the 1024-character cap on per-test-case author explanations for both the
contest (``test_cases``) and Arena (``arena_test_cases``) domains.

Revision ID: 202606200001
Revises: 202606190001
Create Date: 2026-06-20
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202606200001"
down_revision: str | Sequence[str] | None = "202606190001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXPLANATION_COMMENT = "Optional author note explaining why this test case has its expected output."


def upgrade() -> None:
    """Widen the explanation columns to unbounded Text."""
    for table in ("test_cases", "arena_test_cases"):
        op.alter_column(
            table,
            "explanation",
            existing_type=sa.String(length=1024),
            type_=sa.Text(),
            existing_nullable=True,
            existing_comment=_EXPLANATION_COMMENT,
        )


def downgrade() -> None:
    """Restore the String(1024) cap on the explanation columns."""
    for table in ("test_cases", "arena_test_cases"):
        op.alter_column(
            table,
            "explanation",
            existing_type=sa.Text(),
            type_=sa.String(length=1024),
            existing_nullable=True,
            existing_comment=_EXPLANATION_COMMENT,
        )
