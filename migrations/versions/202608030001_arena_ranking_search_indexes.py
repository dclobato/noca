#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add full-text and trigram indexes for Arena ranking search.

The ranking pages search Arena user names and emails and affiliation names.
Only ``arena_users.nome`` was already covered, by ``ix_arena_users_nome_trgm``
from revision 202608020002 — that index is reused as-is here and is
deliberately not recreated, since the earlier revision owns its lifecycle.

The full-text indexes use the ``simple`` configuration because names are proper
nouns: stemming and stopword removal would lose information rather than add
recall. The expressions must stay byte-identical to the ones built in
``arena/services/identity_search_service.py``, or PostgreSQL will not match them
and the indexes go unused.

Revision ID: 202608030001
Revises: 202608020002
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "202608030001"
down_revision: str | Sequence[str] | None = "202608020002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAMES = (
    "ix_arena_users_nome_fts_gin",
    "ix_arena_users_email_normalizado_trgm",
    "ix_arena_affiliations_name_fts_gin",
    "ix_arena_affiliations_name_trgm",
)


def upgrade() -> None:
    """Create the ranking-search full-text and trigram indexes."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX ix_arena_users_nome_fts_gin "
        "ON arena_users USING gin (to_tsvector('simple'::regconfig, coalesce(nome, '')))"
    )
    op.execute(
        "CREATE INDEX ix_arena_users_email_normalizado_trgm ON arena_users USING gin (email_normalizado gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_arena_affiliations_name_fts_gin "
        "ON arena_affiliations USING gin (to_tsvector('simple'::regconfig, coalesce(name, '')))"
    )
    op.execute("CREATE INDEX ix_arena_affiliations_name_trgm ON arena_affiliations USING gin (name gin_trgm_ops)")


def downgrade() -> None:
    """Drop the Arena ranking search indexes."""
    for index_name in _INDEX_NAMES:
        op.drop_index(index_name)
