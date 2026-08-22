#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Structural coverage for PostgreSQL Arena search indexes."""

from __future__ import annotations

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from shared.db_schema.arena import arena_search_indexes

_EXPECTED_INDEX_NAMES = {
    "ix_arena_affiliations_name_fts_gin",
    "ix_arena_affiliations_name_trgm",
    "ix_arena_problems_author_trgm",
    "ix_arena_problems_license_search_vector_gin",
    "ix_arena_problems_license_trgm",
    "ix_arena_problems_number_text_trgm",
    "ix_arena_problems_search_vector_gin",
    "ix_arena_problems_source_trgm",
    "ix_arena_problems_statement_trgm",
    "ix_arena_problems_title_trgm",
    "ix_arena_users_email_normalizado_trgm",
    "ix_arena_users_nome_fts_gin",
    "ix_arena_users_nome_trgm",
}


def test_all_migration_created_search_indexes_are_in_metadata() -> None:
    """Alembic must not mistake intended search indexes for removal candidates."""
    assert {index.name for index in arena_search_indexes} == _EXPECTED_INDEX_NAMES
    for index in arena_search_indexes:
        assert index in index.table.indexes


def test_search_indexes_keep_postgresql_specific_definitions() -> None:
    """Metadata preserves GIN, trigram operator classes, and partial predicates."""
    compiled = {
        index.name: str(CreateIndex(index).compile(dialect=postgresql.dialect())) for index in arena_search_indexes
    }

    assert all(" USING gin " in statement for statement in compiled.values())
    assert "gin_trgm_ops" in compiled["ix_arena_problems_number_text_trgm"]
    assert "to_tsvector" in compiled["ix_arena_problems_search_vector_gin"]
    assert "statement_language" in compiled["ix_arena_problems_search_vector_gin"]

    for index_name in (
        "ix_arena_problems_author_trgm",
        "ix_arena_problems_license_search_vector_gin",
        "ix_arena_problems_license_trgm",
        "ix_arena_problems_source_trgm",
    ):
        assert " WHERE " in compiled[index_name]


def test_search_indexes_are_postgresql_only() -> None:
    """SQLite test schemas must not attempt to execute PostgreSQL expressions."""
    for index in arena_search_indexes:
        assert index._ddl_if is not None  # noqa: SLF001
        assert index._ddl_if.dialect == "postgresql"  # noqa: SLF001
