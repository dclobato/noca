#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add full-text and trigram indexes for Arena problem search.

Revision ID: 202608020002
Revises: 202608020001
Create Date: 2026-08-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "202608020002"
down_revision: str | Sequence[str] | None = "202608020001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAMES = (
    "ix_arena_problems_search_vector_gin",
    "ix_arena_problems_number_text_trgm",
    "ix_arena_problems_title_trgm",
    "ix_arena_problems_statement_trgm",
    "ix_arena_problems_source_trgm",
    "ix_arena_problems_author_trgm",
    "ix_arena_users_nome_trgm",
)


def upgrade() -> None:
    """Create full-text and trigram expression indexes."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        CREATE INDEX ix_arena_problems_search_vector_gin
        ON arena_problems USING gin ((
            setweight(
                to_tsvector(
                    CASE statement_language
                        WHEN 'pt' THEN 'portuguese'::regconfig
                        WHEN 'en' THEN 'english'::regconfig
                        WHEN 'es' THEN 'spanish'::regconfig
                        ELSE 'simple'::regconfig
                    END,
                    coalesce(title, '')
                ),
                'A'
            )
            || setweight(
                to_tsvector(
                    CASE statement_language
                        WHEN 'pt' THEN 'portuguese'::regconfig
                        WHEN 'en' THEN 'english'::regconfig
                        WHEN 'es' THEN 'spanish'::regconfig
                        ELSE 'simple'::regconfig
                    END,
                    coalesce(source, '') || ' ' || coalesce(author, '')
                ),
                'B'
            )
            || setweight(
                to_tsvector(
                    CASE statement_language
                        WHEN 'pt' THEN 'portuguese'::regconfig
                        WHEN 'en' THEN 'english'::regconfig
                        WHEN 'es' THEN 'spanish'::regconfig
                        ELSE 'simple'::regconfig
                    END,
                    coalesce(problem_statement, '')
                ),
                'D'
            )
        ))
        """
    )
    op.execute(
        "CREATE INDEX ix_arena_problems_number_text_trgm "
        "ON arena_problems USING gin ((arena_number::text) gin_trgm_ops)"
    )
    op.execute("CREATE INDEX ix_arena_problems_title_trgm ON arena_problems USING gin (title gin_trgm_ops)")
    op.execute(
        "CREATE INDEX ix_arena_problems_statement_trgm ON arena_problems USING gin (problem_statement gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_arena_problems_source_trgm "
        "ON arena_problems USING gin (source gin_trgm_ops) WHERE source IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_arena_problems_author_trgm "
        "ON arena_problems USING gin (author gin_trgm_ops) WHERE author IS NOT NULL"
    )
    op.execute("CREATE INDEX ix_arena_users_nome_trgm ON arena_users USING gin (nome gin_trgm_ops)")


def downgrade() -> None:
    """Drop Arena problem search indexes."""
    for index_name in _INDEX_NAMES:
        op.drop_index(index_name)
