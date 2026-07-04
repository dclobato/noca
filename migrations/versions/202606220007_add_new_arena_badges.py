#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add new Arena gamification badges to the arenabadge enum.

Adds language-count, first-solver, problem-set hand-in, non-AC burst, sequence,
solve-rate, and presentation-error recovery badge values to the ``arenabadge``
PostgreSQL enum type. ``ALTER TYPE ... ADD VALUE`` cannot run inside a
transaction block, so it is issued in an autocommit block.

Revision ID: 202606220007
Revises: 202606220006
Create Date: 2026-06-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "202606220007"
down_revision: str | Sequence[str] | None = "202606220006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_BADGE_VALUES = (
    "firsttohandin",
    "firstsolver",
    "3languages",
    "5languages",
    "10languages",
    "lococoder",
    "thisistheway",
    "rockcracker",
    "almostlate",
    "trimmer",
)


def upgrade() -> None:
    """Add the new Arena badge values to the arenabadge enum type."""
    with op.get_context().autocommit_block():
        for value in _NEW_BADGE_VALUES:
            op.execute(f"ALTER TYPE arenabadge ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    """No-op.

    PostgreSQL enum values cannot be removed safely without recreating the enum
    and rewriting dependent columns, so the labels are intentionally kept.
    """
