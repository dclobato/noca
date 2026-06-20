"""Add Arena user preferred language locale.

Revision ID: 202605260001
Revises: 622c33190b72
Create Date: 2026-05-26
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605260001"
down_revision: str | Sequence[str] | None = "622c33190b72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the user locale preference used by AI review and future i18n."""
    op.add_column(
        "arena_users",
        sa.Column(
            "prefered_language",
            sa.String(length=5),
            nullable=False,
            server_default="en-US",
            comment="User's preferred interface and AI-response language locale",
        ),
    )
    op.create_check_constraint(
        "ck_arena_users_prefered_language",
        "arena_users",
        "prefered_language IN ('en-US', 'pt-BR')",
    )


def downgrade() -> None:
    """Remove the user locale preference."""
    op.drop_constraint("ck_arena_users_prefered_language", "arena_users", type_="check")
    op.drop_column("arena_users", "prefered_language")
