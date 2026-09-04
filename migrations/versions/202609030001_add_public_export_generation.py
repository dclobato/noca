#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add the public problem-package export cache counter.

``public_export_generation`` is the cache key for the contestant-facing problem
package. A request serves the cached ZIP only while the counter recorded in its
sidecar still equals the problem row's current value, so a stale file on any
replica is detected by reading the row rather than by cross-replica invalidation.

It is deliberately **not** ``artifact_generation``, which stays scoped to
crash recovery. Reusing that fence would be unsafe: its bump runs inside the
caller's open transaction, so a Save that crashes before commit rolls back and
releases the row lock, letting a transaction blocked behind it compute the very
integer the crashed Save's journal recorded as its expected value. Recovery's
``stored >= expected`` check cannot tell the two apart and would treat a
never-committed Save as landed, keeping promoted files whose rows never existed.
Nothing ever infers filesystem-commit state from the counter added here, so a
coincidental collision costs one stale cache read, corrected by the next bump.

The column lands on both problem tables so the bump can stay inside the
domain-agnostic save swap. Only the Web export cache reads it today.

A handful of increments per problem edit keeps this low-churn: the counter rides
along with writes the row already takes, so it warrants no per-table autovacuum
tuning of its own.

Revision ID: 202609030001
Revises: 202609020002
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202609030001"
down_revision: str | Sequence[str] | None = "202609020002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Byte-identical to the comment in shared/db_schema/problem.py and
# shared/db_schema/arena/arena_problems.py, so metadata and database agree.
_EXPORT_GENERATION_COMMENT = "Monotonic counter bumped by every change that alters the public problem package."

_PROBLEM_TABLES: tuple[str, ...] = ("problems", "arena_problems")


def upgrade() -> None:
    """Add ``public_export_generation`` to both problem tables."""
    for table in _PROBLEM_TABLES:
        op.add_column(
            table,
            sa.Column(
                "public_export_generation",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
                comment=_EXPORT_GENERATION_COMMENT,
            ),
        )


def downgrade() -> None:
    """Remove ``public_export_generation`` from both problem tables."""
    for table in reversed(_PROBLEM_TABLES):
        op.drop_column(table, "public_export_generation")
