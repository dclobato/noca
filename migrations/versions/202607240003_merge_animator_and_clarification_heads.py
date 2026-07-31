"""Merge the Animator and general-clarification migration branches.

Revision ID: 202607240003
Revises: 202607220001, 202607240002
Create Date: 2026-07-24 03:15:00.000000
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

revision: str = "202607240003"
down_revision: str | Sequence[str] | None = ("202607220001", "202607240002")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge both migration branches without additional schema changes."""


def downgrade() -> None:
    """Split the migration graph without additional schema changes."""
