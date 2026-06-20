#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""arena user ranking_visible

Revision ID: 202606130001
Revises: 202606120003
Create Date: 2026-06-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606130001"
down_revision: str | Sequence[str] | None = "202606120003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "arena_users",
        sa.Column(
            "ranking_visible",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="User consents to appear in the public ranking; rating is still computed when False",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("arena_users", "ranking_visible")
