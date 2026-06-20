#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""arena user can_edit

Revision ID: 202606050001
Revises: 202606040005
Create Date: 2026-06-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "202606050001"
down_revision: Union[str, Sequence[str], None] = "202606040005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "arena_users",
        sa.Column(
            "can_edit",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="User may add/edit problems on the Arena problem base (admins always may)",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("arena_users", "can_edit")
