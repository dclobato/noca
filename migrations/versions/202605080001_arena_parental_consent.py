"""Add parental consent fields to arena users.

Revision ID: 202605080001
Revises: 202604290001
Create Date: 2026-05-08 00:01:00.000000
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605080001"
down_revision: str | Sequence[str] | None = "202604290001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add parental consent metadata and backfill known adult accounts."""
    op.add_column(
        "arena_users",
        sa.Column("email_responsavel_legal", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "arena_users",
        sa.Column(
            "consentimento_responsavel",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "arena_users",
        sa.Column("dta_consentimento_responsavel", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute(
        sa.text(
            """
            UPDATE arena_users
               SET consentimento_responsavel = true
             WHERE dta_nascimento IS NOT NULL
               AND dta_nascimento <= (CURRENT_DATE - INTERVAL '18 years')
            """
        )
    )


def downgrade() -> None:
    """Remove parental consent metadata."""
    op.drop_column("arena_users", "dta_consentimento_responsavel")
    op.drop_column("arena_users", "consentimento_responsavel")
    op.drop_column("arena_users", "email_responsavel_legal")
