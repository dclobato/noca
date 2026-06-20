#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Arena class registration request denial_reason

Revision ID: 202606040003
Revises: 202606040002
Create Date: 2026-06-04 00:03:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606040003"
down_revision: Union[str, Sequence[str], None] = "202606040002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "arena_class_registration_requests",
        sa.Column(
            "denial_reason",
            sa.String(256),
            nullable=True,
            comment="Optional reason given by the teacher/admin when denying the request",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("arena_class_registration_requests", "denial_reason")
