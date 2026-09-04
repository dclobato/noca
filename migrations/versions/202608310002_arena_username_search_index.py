#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add the trigram index backing Arena username search.

The public ranking search gained username branches when the minor shield landed:
an age-shielded user cannot be matched by their legal name, so the username is
the only handle by which they stay findable. Both branches -- ``ILIKE '%q%'``
and the ``%`` similarity operator -- need a GIN trigram index. The ``UNIQUE``
B-tree that ``username`` already carries from 202608310001 serves neither: a
B-tree cannot answer a leading-wildcard ``LIKE``, and it knows nothing of
``pg_trgm``. One trigram index covers both, exactly as
``ix_arena_users_email_normalizado_trgm`` covers today's email branch.

Username **full-text** search is deliberately not added: it would require a
second expression index alongside the ``to_tsvector`` one on ``nome``, whose
expression must stay byte-identical to revision 202608030001.

The index is also declared in ``shared/db_schema/arena/arena_search_indexes.py``
under ``ddl_if(dialect="postgresql")``. Without that metadata twin, the next
``alembic revision --autogenerate`` would see an index in the database that the
metadata does not know about and propose dropping it.

No autovacuum tuning: this adds no table, and ``arena_users`` is low-churn
reference data already on the server-wide defaults.

This file is short because its DDL is short. It is not padded to the 100-line
band that ``AGENTS.md`` asks of source modules -- an Alembic revision is a
fixed-shape artifact, and filler would add exactly the noise that rule exists to
prevent. Compare revision 202608030001, which creates four indexes in 64 lines.

Revision ID: 202608310002
Revises: 202608310001
Create Date: 2026-08-31
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "202608310002"
down_revision: str | Sequence[str] | None = "202608310001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USERNAME_TRGM_INDEX = "ix_arena_users_username_trgm"


def upgrade() -> None:
    """Create the Arena username trigram search index."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # Written out rather than interpolated, so the DDL this file executes is the
    # literal string a reader -- and the drift test in tests/arena -- can grep.
    op.execute("CREATE INDEX ix_arena_users_username_trgm ON arena_users USING gin (username gin_trgm_ops)")


def downgrade() -> None:
    """Drop the Arena username trigram search index."""
    op.drop_index(USERNAME_TRGM_INDEX)
