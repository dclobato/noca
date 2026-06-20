"""Add arena_number and set disabled-by-default for arena_problems.

Revision ID: 202605230003
Revises: 202605230002
Create Date: 2026-05-23 00:03:00.000000

"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605230003"
down_revision: str | Sequence[str] | None = "202605230002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the public number and set new Arena problems disabled by default."""
    op.execute(
        sa.text(
            "CREATE SEQUENCE arena_problem_arena_number_seq "
            "START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1"
        )
    )
    op.add_column(
        "arena_problems",
        sa.Column(
            "arena_number",
            sa.Integer(),
            nullable=False,
            server_default=sa.text(
                "nextval('arena_problem_arena_number_seq'::regclass)"
            ),
            comment="Sequential public Arena problem number. Unique and never reused.",
        ),
    )
    op.execute(
        sa.text(
            "ALTER SEQUENCE arena_problem_arena_number_seq "
            "OWNED BY arena_problems.arena_number"
        )
    )
    op.create_unique_constraint(
        "uq_arena_problems_arena_number",
        "arena_problems",
        ["arena_number"],
    )
    op.create_check_constraint(
        "ck_arena_problems_arena_number_positive",
        "arena_problems",
        "arena_number >= 1",
    )
    op.alter_column(
        "arena_problems",
        "enabled",
        existing_type=sa.Boolean(),
        existing_nullable=False,
        server_default=sa.text("false"),
    )


def downgrade() -> None:
    """Restore the old enabled default and remove the public Arena problem number."""
    op.alter_column(
        "arena_problems",
        "enabled",
        existing_type=sa.Boolean(),
        existing_nullable=False,
        server_default=sa.text("true"),
    )
    op.drop_constraint(
        "ck_arena_problems_arena_number_positive",
        "arena_problems",
        type_="check",
    )
    op.drop_constraint(
        "uq_arena_problems_arena_number",
        "arena_problems",
        type_="unique",
    )
    op.drop_column("arena_problems", "arena_number")
    op.execute(sa.text("DROP SEQUENCE IF EXISTS arena_problem_arena_number_seq"))
