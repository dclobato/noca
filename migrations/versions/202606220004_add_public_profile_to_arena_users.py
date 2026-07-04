#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add public_profile to arena_users.

Adds the opt-in flag for a future public profile page. The flag is meaningful
only when ``ranking_visible`` is also True; the application layer enforces that
invariant by coercing ``public_profile`` to False whenever ``ranking_visible``
is False.

Revision ID: 202606220004
Revises: 202606220003
Create Date: 2026-06-22
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202606220004"
down_revision: str | Sequence[str] | None = "202606220003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the public_profile column to arena_users."""
    op.add_column(
        "arena_users",
        sa.Column(
            "public_profile",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="User opts in to a public profile page; requires ranking_visible=True",
        ),
    )


def downgrade() -> None:
    """Remove the public_profile column from arena_users."""
    op.drop_column("arena_users", "public_profile")