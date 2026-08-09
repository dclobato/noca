#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Arena ranking-search candidate expressions."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession
from werkzeug.security import generate_password_hash

import arena.models.arena_users  # noqa: F401
from arena.models.arena_affiliations import ArenaAffiliation
from arena.models.arena_users import ArenaUser
from arena.services.identity_search_service import (
    _AFFILIATION_NAME_VECTOR_SQL,
    _USER_NAME_VECTOR_SQL,
    _postgres_affiliation_candidates,
    _postgres_user_candidates,
    affiliation_relevance_ordering,
    prepare_affiliation_search,
    prepare_user_search,
    user_relevance_ordering,
)
from arena.services.ranking_service import get_ranked_affiliations_paginated, get_ranked_users_paginated
from shared.enumerations import ArenaRole

_MIGRATION = (
    Path(__file__).resolve().parents[2] / "migrations" / "versions" / "202608030001_arena_ranking_search_indexes.py"
)

_TEST_PASSWORD = "TestPass1!"


class _PostgresSession:
    """Stand-in exposing only the dialect lookup ``user_relevance_ordering`` performs.

    The helper issues no I/O, so a real PostgreSQL connection would buy nothing
    here beyond making the test skippable.
    """

    def get_bind(self) -> Any:
        """Return an object whose ``dialect`` reports PostgreSQL."""
        return SimpleNamespace(dialect=postgresql.dialect())  # type: ignore[no-untyped-call]


def _postgres_sql(statement: object) -> str:
    """Compile a candidate selectable with the PostgreSQL dialect and inlined binds."""
    return str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )


async def _make_user(
    session: AsyncSession,
    *,
    name: str,
    email: str,
    user_rating: int = 100,
    affiliation_id: str | None = None,
) -> ArenaUser:
    """Persist one eligible, ranking-visible Arena user."""
    user = ArenaUser(
        nome=name,
        email_normalizado=email,
        password_hash=generate_password_hash(_TEST_PASSWORD, method="pbkdf2:sha256:1000"),
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        ranking_visible=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        com_foto=False,
        usa_2fa=False,
        precisa_trocar_senha=False,
        session_version=0,
        user_rating=user_rating,
        solved_problems=5,
        affiliation_id=affiliation_id,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def test_user_search_unions_one_independently_indexable_branch_per_column() -> None:
    sql = _postgres_sql(_postgres_user_candidates("Lovelce"))

    assert "to_tsvector('simple'::regconfig, coalesce(search_arena_user.nome, ''))" in sql
    assert "websearch_to_tsquery(CAST('simple' AS REGCONFIG)" in sql
    assert "search_arena_user.nome ILIKE" in sql
    assert "search_arena_user.email_normalizado ILIKE" in sql
    # Four single-column branches: FTS, name substring, email substring, name fuzzy.
    assert sql.count("UNION") == 3


def test_websearch_operators_suppress_every_fallback_branch() -> None:
    """Operator queries must not be resurrected by substring or fuzzy matching.

    Both entities collapse to a single branch here, so this also guards the
    single-branch path that must not emit a one-arm ``UNION``.
    """
    for query in ('"Ada Lovelace"', "maria -silva", "ada OR grace"):
        sql = _postgres_sql(_postgres_user_candidates(query))

        assert "websearch_to_tsquery" in sql
        assert "UNION" not in sql
        assert " ILIKE " not in sql
        assert "search_arena_user.nome %%" not in sql


def test_short_queries_skip_the_fuzzy_branch() -> None:
    sql = _postgres_sql(_postgres_user_candidates("ab"))

    assert "websearch_to_tsquery" in sql
    assert "search_arena_user.nome ILIKE" in sql
    assert "search_arena_user.email_normalizado ILIKE" in sql
    # FTS plus the two substring branches, with no fuzzy branch.
    assert sql.count("UNION") == 2
    assert "search_arena_user.nome %%" not in sql


def test_email_is_matched_by_substring_only() -> None:
    """An email is an exact identifier; fuzzy matching it would only add noise."""
    sql = _postgres_sql(_postgres_user_candidates("lovelace"))

    assert "search_arena_user.email_normalizado ILIKE" in sql
    assert "search_arena_user.email_normalizado %%" not in sql
    assert "similarity(" not in sql


def test_affiliation_search_targets_the_name_column_only() -> None:
    sql = _postgres_sql(_postgres_affiliation_candidates("universidade"))

    assert "to_tsvector('simple'::regconfig, coalesce(search_arena_affiliation.name, ''))" in sql
    assert "search_arena_affiliation.name ILIKE" in sql
    assert sql.count("UNION") == 2

    phrase_sql = _postgres_sql(_postgres_affiliation_candidates('"universidade federal"'))
    assert "UNION" not in phrase_sql
    assert " ILIKE " not in phrase_sql


async def test_sqlite_search_treats_wildcards_literally(session: AsyncSession) -> None:
    candidates = await prepare_user_search(session, "50%_off")
    sql = str(candidates.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))

    assert "50\\%\\_off" in sql
    assert "websearch_to_tsquery" not in sql

    affiliation_sql = str(
        (await prepare_affiliation_search(session, "50%_off")).compile(
            dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "50\\%\\_off" in affiliation_sql


async def test_ranking_search_filters_without_changing_rank(session: AsyncSession) -> None:
    """Search narrows the page but never renumbers it — rank #N stays #N."""
    await _make_user(session, name="Grace Hopper", email="grace@test.example", user_rating=500)
    await _make_user(session, name="Ada Lovelace", email="ada.lovelace@test.example", user_rating=300)
    await _make_user(session, name="Alan Turing", email="alan@test.example", user_rating=100)

    unfiltered = await get_ranked_users_paginated(session)
    assert [item.rank for item in unfiltered.items] == [1, 2, 3]

    by_name = await get_ranked_users_paginated(session, search="lovel")
    assert [item.name for item in by_name.items] == ["Ada Lovelace"]
    assert by_name.items[0].rank == 2
    assert by_name.total == 1

    by_email = await get_ranked_users_paginated(session, search="ada.lovelace@")
    assert [item.name for item in by_email.items] == ["Ada Lovelace"]

    assert (await get_ranked_users_paginated(session, search="zzzznomatch")).total == 0


async def test_affiliation_ranking_search_filters_by_name(session: AsyncSession) -> None:
    session.add(ArenaAffiliation(name="Universidade Federal do Ceara", rating=200))
    session.add(ArenaAffiliation(name="Instituto Tecnologico", rating=100))
    await session.commit()

    matched = await get_ranked_affiliations_paginated(session, search="federal")
    assert [item.name for item in matched.items] == ["Universidade Federal do Ceara"]
    assert matched.items[0].rank == 1

    assert (await get_ranked_affiliations_paginated(session, search="zzzznomatch")).total == 0


async def test_relevance_ordering_is_opt_in_and_dialect_aware(session: AsyncSession) -> None:
    """A blank query has no relevance, and SQLite has no similarity function."""
    assert user_relevance_ordering(session, "   ") == []
    assert affiliation_relevance_ordering(session, "   ") == []

    sqlite_terms = user_relevance_ordering(session, "lovelace")
    assert len(sqlite_terms) == 1
    assert "nome ASC" in str(sqlite_terms[0].compile(dialect=sqlite.dialect()))

    affiliation_terms = affiliation_relevance_ordering(session, "university")
    assert len(affiliation_terms) == 2
    assert str(affiliation_terms[0].compile(dialect=sqlite.dialect())) == "lower(arena_affiliations.name) ASC"
    assert str(affiliation_terms[1].compile(dialect=sqlite.dialect())) == "arena_affiliations.name ASC"


def test_postgres_relevance_ordering_puts_literal_hits_above_fuzzy_ones() -> None:
    """Fuzzy matches must not outrank a row that literally contains the query.

    The autocomplete truncates its list, so a fuzzy match sorting first can push
    the row the user actually typed out of sight entirely.
    """
    literal_hit, similarity_term, name_term = (
        str(term.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))  # type: ignore[no-untyped-call]
        for term in user_relevance_ordering(cast(AsyncSession, _PostgresSession()), "Lovelce")
    )

    # The doubled %% is pyformat paramstyle escaping from compiling a lone
    # fragment; the pattern itself is a single-% substring wrapper.
    assert "CASE WHEN" in literal_hit
    assert "arena_users.nome ILIKE '%%Lovelce%%'" in literal_hit
    assert "arena_users.email_normalizado ILIKE '%%Lovelce%%'" in literal_hit
    assert literal_hit.endswith("THEN 1 ELSE 0 END DESC")
    assert similarity_term.startswith("similarity(arena_users.nome, 'Lovelce')")
    assert similarity_term.endswith("DESC")
    assert name_term == "arena_users.nome ASC"


def test_postgres_affiliation_relevance_ordering_puts_literal_hits_first() -> None:
    """Affiliation autocomplete must rank literal matches before fuzzy noise."""
    literal_hit, similarity_term, name_term, case_tiebreaker = (
        str(term.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))  # type: ignore[no-untyped-call]
        for term in affiliation_relevance_ordering(
            cast(AsyncSession, _PostgresSession()),
            "Federal%",
        )
    )

    assert "arena_affiliations.name ILIKE '%%Federal\\\\%%%%'" in literal_hit
    assert literal_hit.endswith("THEN 1 ELSE 0 END DESC")
    assert similarity_term.startswith("similarity(arena_affiliations.name, 'Federal%%')")
    assert similarity_term.endswith("DESC")
    assert name_term == "lower(arena_affiliations.name) ASC"
    assert case_tiebreaker == "arena_affiliations.name ASC"


def test_ranking_search_migration_uses_transactional_expression_indexes() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")

    assert "CONCURRENTLY" not in source
    assert "ADD COLUMN" not in source
    assert source.count("CREATE INDEX") == 4
    assert "gin_trgm_ops" in source
    # The one-argument to_tsvector reads default_text_search_config and is only
    # STABLE, so PostgreSQL rejects it in an index expression.
    assert "to_tsvector(coalesce" not in source
    assert "'simple'::regconfig" in source
    # ix_arena_users_nome_trgm is owned by 202608020002 and reused, not recreated.
    assert "CREATE INDEX ix_arena_users_nome_trgm" not in source
    assert 'down_revision: str | Sequence[str] | None = "202608020002"' in source


def test_search_vector_expressions_match_the_migration_ddl() -> None:
    """The service expression and the index DDL must stay byte-identical.

    PostgreSQL matches an expression index by comparing the parsed expression,
    so any drift between the two silently downgrades the search to a seq scan.
    """
    source = _MIGRATION.read_text(encoding="utf-8")

    assert _USER_NAME_VECTOR_SQL.replace("arena_users.", "") in source
    assert _AFFILIATION_NAME_VECTOR_SQL.replace("arena_affiliations.", "") in source
