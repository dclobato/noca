#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add Terms of Service and Privacy Policy acceptance columns to arena_users.

Adds:
  - aceitou_termos_privacidade  (Boolean, NOT NULL, default false)
  - dta_aceitacao_termos_privacidade  (DateTime with timezone, nullable)

Existing rows receive aceitou_termos_privacidade = false intentionally,
since those users were registered before an explicit acceptance checkbox
was introduced.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202605221200"
down_revision = "202605191806"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ToS and Privacy Policy acceptance columns."""
    op.add_column(
        "arena_users",
        sa.Column(
            "aceitou_termos_privacidade",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="User explicitly accepted the Terms of Service and Privacy Policy during signup",
        ),
    )
    op.add_column(
        "arena_users",
        sa.Column(
            "dta_aceitacao_termos_privacidade",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp (UTC) when the user accepted the Terms of Service and Privacy Policy",
        ),
    )


def downgrade() -> None:
    """Remove ToS and Privacy Policy acceptance columns."""
    op.drop_column("arena_users", "dta_aceitacao_termos_privacidade")
    op.drop_column("arena_users", "aceitou_termos_privacidade")
