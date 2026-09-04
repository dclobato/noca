#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""PostgreSQL search indexes shared by the Arena schema metadata."""

from __future__ import annotations

from sqlalchemy import Index, literal_column, text

from .arena_problems import arena_problems
from .arena_users import arena_affiliations, arena_users

_problem_search_vector = (
    """((setweight(to_tsvector(
CASE statement_language
    WHEN 'pt'::statementlanguage THEN 'portuguese'::regconfig
    WHEN 'en'::statementlanguage THEN 'english'::regconfig
    WHEN 'es'::statementlanguage THEN 'spanish'::regconfig
    ELSE 'simple'::regconfig
END, COALESCE(title, ''::character varying)::text), 'A'::"char") || setweight(to_tsvector(
CASE statement_language
    WHEN 'pt'::statementlanguage THEN 'portuguese'::regconfig
    WHEN 'en'::statementlanguage THEN 'english'::regconfig
    WHEN 'es'::statementlanguage THEN 'spanish'::regconfig
    ELSE 'simple'::regconfig
END, (COALESCE(source, ''::character varying)::text || ' '::text) || """
    """COALESCE(author, ''::character varying)::text), 'B'::"char")) || """
    """setweight(to_tsvector(
CASE statement_language
    WHEN 'pt'::statementlanguage THEN 'portuguese'::regconfig
    WHEN 'en'::statementlanguage THEN 'english'::regconfig
    WHEN 'es'::statementlanguage THEN 'spanish'::regconfig
    ELSE 'simple'::regconfig
END, COALESCE(problem_statement, ''::text)), 'D'::"char"))"""
)

arena_search_indexes = (
    Index(
        "ix_arena_problems_search_vector_gin",
        text(_problem_search_vector),
        _table=arena_problems,
        postgresql_using="gin",
    ).ddl_if(dialect="postgresql"),
    Index(
        "ix_arena_problems_number_text_trgm",
        literal_column("(arena_number::text)").label("arena_number_text"),
        _table=arena_problems,
        postgresql_ops={"arena_number_text": "gin_trgm_ops"},
        postgresql_using="gin",
    ).ddl_if(dialect="postgresql"),
    Index(
        "ix_arena_problems_title_trgm",
        arena_problems.c.title,
        postgresql_ops={"title": "gin_trgm_ops"},
        postgresql_using="gin",
    ).ddl_if(dialect="postgresql"),
    Index(
        "ix_arena_problems_statement_trgm",
        arena_problems.c.problem_statement,
        postgresql_ops={"problem_statement": "gin_trgm_ops"},
        postgresql_using="gin",
    ).ddl_if(dialect="postgresql"),
    Index(
        "ix_arena_problems_source_trgm",
        arena_problems.c.source,
        postgresql_ops={"source": "gin_trgm_ops"},
        postgresql_using="gin",
        postgresql_where=arena_problems.c.source.is_not(None),
    ).ddl_if(dialect="postgresql"),
    Index(
        "ix_arena_problems_author_trgm",
        arena_problems.c.author,
        postgresql_ops={"author": "gin_trgm_ops"},
        postgresql_using="gin",
        postgresql_where=arena_problems.c.author.is_not(None),
    ).ddl_if(dialect="postgresql"),
    Index(
        "ix_arena_problems_license_search_vector_gin",
        text("to_tsvector('simple'::regconfig, coalesce(license, ''))"),
        _table=arena_problems,
        postgresql_using="gin",
        postgresql_where=arena_problems.c.license.is_not(None),
    ).ddl_if(dialect="postgresql"),
    Index(
        "ix_arena_problems_license_trgm",
        arena_problems.c.license,
        postgresql_ops={"license": "gin_trgm_ops"},
        postgresql_using="gin",
        postgresql_where=arena_problems.c.license.is_not(None),
    ).ddl_if(dialect="postgresql"),
    Index(
        "ix_arena_users_nome_trgm",
        arena_users.c.nome,
        postgresql_ops={"nome": "gin_trgm_ops"},
        postgresql_using="gin",
    ).ddl_if(dialect="postgresql"),
    Index(
        "ix_arena_users_nome_fts_gin",
        text("to_tsvector('simple'::regconfig, coalesce(nome, ''))"),
        _table=arena_users,
        postgresql_using="gin",
    ).ddl_if(dialect="postgresql"),
    Index(
        "ix_arena_users_email_normalizado_trgm",
        arena_users.c.email_normalizado,
        postgresql_ops={"email_normalizado": "gin_trgm_ops"},
        postgresql_using="gin",
    ).ddl_if(dialect="postgresql"),
    Index(
        "ix_arena_users_username_trgm",
        arena_users.c.username,
        postgresql_ops={"username": "gin_trgm_ops"},
        postgresql_using="gin",
    ).ddl_if(dialect="postgresql"),
    # Partial, like the nullable problem columns: only accounts that required
    # parental consent hold a guardian address. ILIKE is strict, so a NULL can
    # never match and the predicate costs the admin search nothing.
    Index(
        "ix_arena_users_email_responsavel_legal_trgm",
        arena_users.c.email_responsavel_legal,
        postgresql_ops={"email_responsavel_legal": "gin_trgm_ops"},
        postgresql_using="gin",
        postgresql_where=arena_users.c.email_responsavel_legal.is_not(None),
    ).ddl_if(dialect="postgresql"),
    Index(
        "ix_arena_affiliations_name_fts_gin",
        text("to_tsvector('simple'::regconfig, coalesce(name, ''))"),
        _table=arena_affiliations,
        postgresql_using="gin",
    ).ddl_if(dialect="postgresql"),
    Index(
        "ix_arena_affiliations_name_trgm",
        arena_affiliations.c.name,
        postgresql_ops={"name": "gin_trgm_ops"},
        postgresql_using="gin",
    ).ddl_if(dialect="postgresql"),
)
