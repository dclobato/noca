"""move repetitions to problem language limits

Revision ID: 202604091730
Revises: b7d4e2f91c30
Create Date: 2026-04-09 17:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202604091730"
down_revision: str | Sequence[str] | None = "b7d4e2f91c30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "problem_language_limits",
        sa.Column("repetitions", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE problem_language_limits AS pll
            SET repetitions = p.repetitions
            FROM problems AS p
            WHERE p.id = pll.problem_id
            """
        )
    )
    op.alter_column("problem_language_limits", "repetitions", nullable=False)
    op.create_check_constraint(
        "ck_problem_language_limits_repetitions_positive",
        "problem_language_limits",
        "repetitions >= 1",
    )
    op.drop_constraint("ck_problems_repetitions_positive", "problems", type_="check")
    op.drop_column("problems", "repetitions")


def downgrade() -> None:
    op.add_column(
        "problems",
        sa.Column(
            "repetitions",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.create_check_constraint(
        "ck_problems_repetitions_positive",
        "problems",
        "repetitions >= 1",
    )
    op.drop_constraint(
        "ck_problem_language_limits_repetitions_positive",
        "problem_language_limits",
        type_="check",
    )
    op.drop_column("problem_language_limits", "repetitions")
