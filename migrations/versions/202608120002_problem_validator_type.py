#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Store each problem's validation strategy explicitly.

Until now "is this problem interactive" was inferred from the presence of a
``problem_custom_validators`` / ``arena_problem_custom_validators`` row. That
derivation is wrong in both directions: a problem whose validator source was
removed silently became a standard problem judged by the token comparator, and a
standard problem carrying a stale validator row looked interactive.

``validator_type`` replaces the inference with a stored fact. The backfill
preserves today's observable behavior exactly -- any validator row, in any
revision state including pending-only and invalid, means ``interactive``.

``artifact_generation`` lands in the same migration so the schema settles once.
Nothing increments it yet; it is the fence a later editor-save recovery pass uses
to tell whether a save's transaction committed, which problem-row existence
cannot answer for an edit.

No server default on ``validator_type``: every creation path states the strategy
explicitly, so a path that forgets fails loudly instead of silently becoming
standard.

Revision ID: 202608120002
Revises: 202608120001
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202608120002"
down_revision: str | Sequence[str] | None = "202608120001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VALIDATOR_TYPE_VALUES = ("standard", "interactive", "checker")

_TYPE_COMMENT = "Stored, immutable validation strategy. Never inferred from validator source presence."
_GENERATION_COMMENT = "Monotonic fence incremented by each editor save that promotes filesystem artifacts."

# (problem table, custom-validator table) pairs, one per identity domain.
_DOMAINS: tuple[tuple[str, str], ...] = (
    ("problems", "problem_custom_validators"),
    ("arena_problems", "arena_problem_custom_validators"),
)


def _validator_type_enum(*, create_type: bool = True) -> postgresql.ENUM:
    """Return the validation-strategy PG enum type descriptor."""
    return postgresql.ENUM(*_VALIDATOR_TYPE_VALUES, name="problemvalidatortype", create_type=create_type)


def upgrade() -> None:
    """Add and backfill validator_type and artifact_generation on both problem tables."""
    bind = op.get_bind()
    # Created exactly once: the second add_column below references it with
    # create_type=False, since a duplicate CREATE TYPE would fail the migration.
    _validator_type_enum().create(bind, checkfirst=True)

    for problem_table, validator_table in _DOMAINS:
        op.add_column(
            problem_table,
            sa.Column(
                "validator_type",
                _validator_type_enum(create_type=False),
                nullable=True,
                comment=_TYPE_COMMENT,
            ),
        )
        op.execute(
            sa.text(
                f"UPDATE {problem_table} SET validator_type = 'interactive' "  # noqa: S608 - fixed identifiers
                f"WHERE id IN (SELECT problem_id FROM {validator_table})"
            )
        )
        op.execute(
            sa.text(
                f"UPDATE {problem_table} SET validator_type = 'standard' "  # noqa: S608 - fixed identifiers
                "WHERE validator_type IS NULL"
            )
        )
        op.alter_column(problem_table, "validator_type", nullable=False)
        op.add_column(
            problem_table,
            sa.Column(
                "artifact_generation",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
                comment=_GENERATION_COMMENT,
            ),
        )


def downgrade() -> None:
    """Drop both columns from both problem tables, then the enum type."""
    for problem_table, _ in _DOMAINS:
        op.drop_column(problem_table, "artifact_generation")
        op.drop_column(problem_table, "validator_type")
    _validator_type_enum().drop(op.get_bind(), checkfirst=True)
