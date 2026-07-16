"""shared: Add sample interactions for problems with a custom validator.

Revision ID: 202607150001
Revises: 202607140001
Create Date: 2026-07-15 00:01:00.000000

Interactive problems present author-written sample conversations instead of
sample test cases. The data migration therefore also demotes every public test
case of a problem that already has a configured validator to secret. That flip
is not reversible: ``downgrade`` only drops the new tables.

"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607150001"
down_revision: str | Sequence[str] | None = "202607140001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    ("problem_sample_interactions", "problems", "problem", "problem_custom_validators", "test_cases"),
    (
        "arena_sample_interactions",
        "arena_problems",
        "arena",
        "arena_problem_custom_validators",
        "arena_test_cases",
    ),
)


def upgrade() -> None:
    """Upgrade schema."""
    for table_name, problems_table, prefix, validators_table, test_cases_table in _TABLES:
        op.create_table(
            table_name,
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("problem_id", sa.String(length=36), nullable=False),
            sa.Column(
                "ordinal",
                sa.Integer(),
                nullable=False,
                comment="1-based display order among the problem's sample interactions.",
            ),
            sa.Column(
                "transcript",
                sa.JSON(),
                nullable=False,
                comment="{'lines': [{'dir': 'user'|'validator', 'line': str}], 'truncated': bool}",
            ),
            sa.Column(
                "explanation",
                sa.Text(),
                nullable=True,
                comment="Optional author note explaining this sample interaction.",
            ),
            sa.Column(
                "hidden_at",
                sa.DateTime(timezone=True),
                nullable=True,
                comment="Time when the interaction was hidden after its custom validator was removed.",
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["problem_id"], [f"{problems_table}.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("problem_id", "ordinal", name=f"uq_{prefix}_sample_interactions_problem_ordinal"),
            sa.CheckConstraint("ordinal >= 1", name=f"ck_{prefix}_sample_interactions_ordinal_positive"),
        )
        op.create_index(f"ix_{table_name}_problem_id", table_name, ["problem_id"])

        op.execute(
            sa.text(
                f"UPDATE {test_cases_table} SET is_sample = false "
                f"WHERE is_sample AND problem_id IN ("
                f"  SELECT problem_id FROM {validators_table} "
                f"  WHERE active_source IS NOT NULL OR candidate_source IS NOT NULL"
                f")"
            )
        )


def downgrade() -> None:
    """Downgrade schema.

    The demotion of public test cases on interactive problems is intentionally
    not reversed: the original sample flags are not recorded anywhere.
    """
    for table_name, _problems_table, _prefix, _validators_table, _test_cases_table in _TABLES:
        op.drop_index(f"ix_{table_name}_problem_id", table_name=table_name)
        op.drop_table(table_name)
