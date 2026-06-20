#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Arena class allow_self_registration flag

Revision ID: 202606040002
Revises: 202606040001
Create Date: 2026-06-04 00:02:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606040002"
down_revision: Union[str, Sequence[str], None] = "202606040001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "arena_classes",
        sa.Column(
            "allow_self_registration",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="When true, users may discover the class and request to self-register",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("arena_classes", "allow_self_registration")
