"""add email_normalizado column to users

Revision ID: a3f2b1c9d4e6
Revises: e1a9c6d4b5f2
Create Date: 2026-04-07 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f2b1c9d4e6"
down_revision: str | Sequence[str] | None = "e1a9c6d4b5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column(
            "email_normalizado",
            sa.String(180),
            nullable=True,
            comment="Email do usuário para envio de credenciais e informações pré ou pós contest",
        ),
    )
    op.create_index(op.f("ix_users_email_normalizado"), "users", ["email_normalizado"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_users_email_normalizado"), table_name="users")
    op.drop_column("users", "email_normalizado")
