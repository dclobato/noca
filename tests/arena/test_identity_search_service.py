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

from sqlalchemy import select
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
    _public_postgres_user_candidates,
    affiliation_relevance_ordering,
    prepare_affiliation_search,
    prepare_public_user_search,
    prepare_user_search,
    user_relevance_ordering,
)
from arena.services.ranking_service import get_ranked_affiliations_paginated, get_ranked_users_paginated
from shared.db_schema.arena import arena_users
from shared.enumerations import ArenaRole

_VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
_MIGRATION = _VERSIONS / "202608030001_arena_ranking_search_indexes.py"
_USERNAME_INDEX_MIGRATION = _VERSIONS / "202608310002_arena_username_search_index.py"

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

    # Rows are matched by id, not by name: these adults never opted in to
    # publishing their legal name, so ``item.name`` is now their username.
    ada = next(item for item in unfiltered.items if item.rank == 2)

    by_name = await get_ranked_users_paginated(session, search="lovel")
    assert [item.id for item in by_name.items] == [ada.id]
    assert by_name.items[0].rank == 2
    assert by_name.total == 1

    by_email = await get_ranked_users_paginated(session, search="ada.lovelace@")
    assert [item.id for item in by_email.items] == [ada.id]

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


# ---------------------------------------------------------------------------
# The public (age-shielded) sibling
# ---------------------------------------------------------------------------


async def _make_minor(session: AsyncSession, *, name: str, email: str) -> ArenaUser:
    """Persist an eligible, ranking-visible Arena user who is 13-17 years old."""
    user = await _make_user(session, name=name, email=email)
    user.dta_nascimento = date.today().replace(year=date.today().year - 15)
    await session.commit()
    await session.refresh(user)
    return user


def test_public_user_search_shields_name_branches_and_opens_username_ones() -> None:
    """Every ``nome`` branch is age-gated; every ``username`` branch is not."""
    sql = _postgres_sql(_public_postgres_user_candidates("Lovelce"))

    # Six branches: name FTS, name substring, username substring, email
    # substring, name fuzzy, username fuzzy.
    assert sql.count("UNION") == 5
    assert "search_arena_user.username ILIKE" in sql
    assert "search_arena_user.username %%" in sql
    # Three name branches, each carrying the negated shield (which names
    # dta_nascimento twice: the NULL test and the cutoff comparison).
    assert sql.count("search_arena_user.dta_nascimento") == 6


def test_public_user_search_binds_the_shield_to_the_alias_only() -> None:
    """The shield must correlate to the branch's alias, never to a second FROM.

    Binding it to ``arena_users`` would add an unjoined table and turn each
    branch into a cross join with an uncorrelated age test.
    """
    sql = _postgres_sql(_public_postgres_user_candidates("lovelace"))

    assert sql.count("arena_users") == sql.count("arena_users AS search_arena_user")


def test_public_user_search_adds_no_username_full_text_branch() -> None:
    """Username FTS would need a second expression index, which is out of scope."""
    sql = _postgres_sql(_public_postgres_user_candidates("lovelace"))

    assert sql.count("to_tsvector") == 1
    assert "coalesce(search_arena_user.username" not in sql


async def test_portable_public_search_also_shields_the_name(session: AsyncSession) -> None:
    """The SQLite path is the one the whole suite runs on -- it must shield too."""
    candidates = await prepare_public_user_search(session, "lovelace")
    sql = str(candidates.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))

    assert "lower(search_arena_user.username) LIKE" in sql
    assert "search_arena_user.dta_nascimento" in sql


async def test_shielded_user_is_unfindable_by_their_real_name(session: AsyncSession) -> None:
    """The public ranking must not confirm that a real name belongs to a minor.

    Rendering a pseudonym while still answering "is this name in the ranking?"
    would reconstruct the shield's own secret.
    """
    minor = await _make_minor(session, name="Joana Menorista", email="joana@test.example")

    by_real_name = await get_ranked_users_paginated(session, search="Menorista")
    assert by_real_name.total == 0

    by_username = await get_ranked_users_paginated(session, search=minor.username)
    assert [item.id for item in by_username.items] == [minor.id]


async def test_adult_stays_findable_by_their_real_name(session: AsyncSession) -> None:
    """The shield narrows the public search for minors only."""
    await _make_user(session, name="Ada Lovelace", email="ada.public@test.example")

    found = await get_ranked_users_paginated(session, search="Lovelace")

    assert found.total == 1


async def test_teacher_scoped_search_still_finds_a_shielded_student(session: AsyncSession) -> None:
    """``prepare_user_search`` is the teacher path and keeps matching real names.

    A teacher looking a student up by the name on the roll has a legitimate
    basis; only the public surfaces are shielded.
    """
    minor = await _make_minor(session, name="Joana Menorista", email="joana.teacher@test.example")

    candidates = await prepare_user_search(session, "Menorista")
    matched = (await session.execute(select(arena_users.c.id).where(arena_users.c.id.in_(candidates)))).scalars().all()

    assert minor.id in set(matched)


def test_username_search_index_backs_the_username_branches() -> None:
    """The ilike and trigram username branches need a GIN trigram index.

    The UNIQUE B-tree from 202608310001 answers neither a leading-wildcard
    ``LIKE`` nor the ``%`` operator, so the branches would seq-scan without it.
    """
    source = _USERNAME_INDEX_MIGRATION.read_text(encoding="utf-8")

    assert "CONCURRENTLY" not in source
    assert "CREATE INDEX ix_arena_users_username_trgm ON arena_users USING gin (username gin_trgm_ops)" in source
    assert 'down_revision: str | Sequence[str] | None = "202608310001"' in source
