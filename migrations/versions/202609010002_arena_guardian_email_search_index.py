#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add the trigram index backing guardian-email search on the admin user list.

``admin_user_service.list_users_paginated`` searches four columns with
``ILIKE '%q%'``. Three were already index-eligible -- ``nome``,
``email_normalizado`` and, since 202608310002, ``username`` all carry GIN
trigram indexes, which is what serves a leading-wildcard ``LIKE``. The fourth,
``email_responsavel_legal``, carried no index of any kind, so it could only be
answered by a sequential scan; and because the four branches are ``OR``ed into
one predicate, that single unindexed branch is enough to push the planner into
scanning the table for the whole query rather than combining bitmap scans.

The index is **partial** (``WHERE email_responsavel_legal IS NOT NULL``),
matching how the nullable problem columns are indexed (``ix_arena_problems_
source_trgm`` and its siblings). That is both smaller and correct here: only
accounts that required parental consent hold a guardian address, so on a mature
install most rows are NULL. The partial predicate does not cost the query its
index, because ``ILIKE`` is strict -- a NULL can never match -- so PostgreSQL's
predicate prover knows any matching row satisfies ``IS NOT NULL``.

No full-text index: a guardian address is looked up whole or by fragment, never
by word, and an ``email_normalizado``-style trigram index is the shape that
answers both.

The index is also declared in ``shared/db_schema/arena/arena_search_indexes.py``
under ``ddl_if(dialect="postgresql")``. Without that metadata twin, the next
``alembic revision --autogenerate`` would see an index in the database that the
metadata does not know about and propose dropping it.

No autovacuum tuning: this adds no table, and ``arena_users`` is low-churn
reference data already on the server-wide defaults.

This file is short because its DDL is short; it is not padded to the 100-line
band ``AGENTS.md`` asks of source modules, for the reason given in revision
202608310002.

Revision ID: 202609010002
Revises: 202609010001
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "202609010002"
down_revision: str | Sequence[str] | None = "202609010001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GUARDIAN_EMAIL_TRGM_INDEX = "ix_arena_users_email_responsavel_legal_trgm"


def upgrade() -> None:
    """Create the guardian-email trigram search index."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # Written out rather than interpolated, so the DDL this file executes is the
    # literal string a reader -- and the drift test -- can grep.
    op.execute(
        "CREATE INDEX ix_arena_users_email_responsavel_legal_trgm "
        "ON arena_users USING gin (email_responsavel_legal gin_trgm_ops) "
        "WHERE email_responsavel_legal IS NOT NULL"
    )


def downgrade() -> None:
    """Drop the guardian-email trigram search index."""
    op.drop_index(GUARDIAN_EMAIL_TRGM_INDEX)
