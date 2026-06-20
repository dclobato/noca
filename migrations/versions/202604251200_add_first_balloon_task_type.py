"""add_first_balloon_task_type

Revision ID: 202604251200
Revises: e00815b4118a
Create Date: 2026-04-25 12:00:00.000000

"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202604251200"
down_revision: str | Sequence[str] | None = "e00815b4118a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add FIRST_BALLOON to the task enum and enforce one per problem."""
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE taskenum ADD VALUE IF NOT EXISTS 'FIRST_BALLOON'")
    op.create_index(
        "ux_tasks_first_balloon_problem_id",
        "tasks",
        ["problem_id"],
        unique=True,
        postgresql_where=sa.text("type = 'FIRST_BALLOON'"),
        sqlite_where=sa.text("type = 'FIRST_BALLOON'"),
    )


def downgrade() -> None:
    """Drop the uniqueness guard.

    PostgreSQL enum values cannot be removed safely without recreating the enum
    and rewriting dependent columns, so the enum label is intentionally kept.
    """
    op.drop_index("ux_tasks_first_balloon_problem_id", table_name="tasks")
