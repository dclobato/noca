"""add allow_print_requests flag to contests

Revision ID: e1a9c6d4b5f2
Revises: c7f2a9b4e1d3
Create Date: 2026-04-07 01:50:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1a9c6d4b5f2"
down_revision: str | Sequence[str] | None = "c7f2a9b4e1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "contests",
        sa.Column(
            "allow_print_requests",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment="Indica se as equipes podem criar tarefas PRINT para solicitar impressao de codigo",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("contests", "allow_print_requests")
