"""Add stdout flush hints to language registry rows.

Revision ID: 202607130001
Revises: 202607120001
Create Date: 2026-07-13 00:01:00.000000

"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from shared.language_registry import default_language_seed_rows

revision: str = "202607130001"
down_revision: str | Sequence[str] | None = "202607120001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "languages",
        sa.Column(
            "stdout_flush_hint",
            sa.String(length=255),
            nullable=True,
            comment="Language-specific hint for flushing standard output in interactive-validator problems.",
        ),
    )

    language_table = sa.table(
        "languages",
        sa.column("id", sa.String(length=64)),
        sa.column("stdout_flush_hint", sa.String(length=255)),
    )
    for row in default_language_seed_rows():
        op.execute(
            language_table.update()
            .where(language_table.c.id == row["id"])
            .values(stdout_flush_hint=row["stdout_flush_hint"])
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("languages", "stdout_flush_hint")
