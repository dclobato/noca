#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for shared Arena catalog problem-search expressions."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import ArenaProblem
from arena.routes.admin_problem_form_views import problem_list_url
from arena.routes.admin_problems import _effective_problem_sort
from arena.routes.problems import _safe_sort
from arena.services.admin_problem_service import DEFAULT_SORT
from arena.services.problem_search_service import (
    _SEARCH_VECTOR_SQL,
    _TITLE_SEARCH_VECTOR_SQL,
    _exact_number_match,
    _postgres_problem_picker_search,
    _postgres_search,
    prepare_problem_picker_search,
    prepare_problem_search,
)

_MIGRATION = (
    Path(__file__).resolve().parents[2] / "migrations" / "versions" / "202608020002_arena_problem_full_text_search.py"
)


def test_postgresql_search_uses_row_language_and_indexes_free_text_author() -> None:
    expressions = _postgres_search("running -planes")
    statement = select(ArenaProblem.id).where(expressions.predicate)
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "coalesce(search_problem.author, '')" in sql
    assert "search_problem.statement_language = 'pt' AND" in sql
    assert "websearch_to_tsquery(CAST('portuguese' AS REGCONFIG)" in sql
    assert "search_problem.statement_language = 'en' AND" in sql
    assert "websearch_to_tsquery(CAST('english' AS REGCONFIG)" in sql
    assert "search_owner.nome" not in sql
    assert " ILIKE " not in sql
    assert "search_problem.title %%" not in sql

    ordinary_sql = str(
        select(ArenaProblem.id)
        .where(_postgres_search("Lovelce").predicate)
        .compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "search_owner.nome" in ordinary_sql
    assert " ILIKE " in ordinary_sql


async def test_sqlite_search_treats_wildcards_literally_and_has_zero_rank(
    session: AsyncSession,
) -> None:
    expressions = await prepare_problem_search(session, "50%_off")
    statement = select(ArenaProblem.id).where(expressions.predicate)
    sql = str(
        statement.compile(
            dialect=sqlite.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "50\\%\\_off" in sql
    assert expressions.full_text_rank.value == 0.0
    assert expressions.trigram_rank.value == 0.0


async def test_sqlite_problem_picker_search_is_narrow_and_escapes_wildcards(
    session: AsyncSession,
) -> None:
    expressions = await prepare_problem_picker_search(session, "50%_off")
    sql = str(
        select(ArenaProblem.id)
        .where(expressions.predicate)
        .compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True})
    )

    assert "50\\%\\_off" in sql
    assert "arena_problems.arena_number" in sql
    assert "arena_problems.title" in sql
    assert "arena_problems.problem_statement" not in sql
    assert "arena_problems.source" not in sql
    assert "arena_problems.author" not in sql
    assert expressions.full_text_rank.value == 0.0
    assert expressions.trigram_rank.value == 0.0


def test_postgresql_problem_picker_search_uses_title_and_number_indexes() -> None:
    expressions = _postgres_problem_picker_search("Airplne")
    sql = str(
        select(ArenaProblem.id)
        .where(expressions.predicate)
        .compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "coalesce(picker_problem.title, '')" in sql
    assert "picker_problem.arena_number AS TEXT" in sql
    assert "picker_problem.title ILIKE" in sql
    assert "picker_problem.title %% 'Airplne'" in sql
    assert "picker_problem.problem_statement ILIKE" not in sql
    assert "picker_problem.source ILIKE" not in sql
    assert "picker_problem.author ILIKE" not in sql

    phrase_sql = str(
        select(ArenaProblem.id)
        .where(_postgres_problem_picker_search('"Airplane Routes"').predicate)
        .compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )
    assert " ILIKE " not in phrase_sql
    assert "picker_problem.title %%" not in phrase_sql


def test_problem_routes_default_to_relevance_only_while_searching() -> None:
    assert _safe_sort(None, "graph") == "relevance"
    assert _safe_sort("title_asc", "graph") == "title_asc"
    assert _safe_sort("relevance", "") == "number_asc"

    assert _effective_problem_sort(None, "graph") == "relevance"
    assert _effective_problem_sort("rating_desc", "graph") == "rating_desc"
    assert _effective_problem_sort("relevance", "") == "number_asc"


def test_exact_number_match_rejects_non_decimal_and_out_of_int4_range() -> None:
    assert str(_exact_number_match("12")) == "arena_problems.arena_number = :arena_number_1"
    assert str(_exact_number_match("1_0")) == "false"
    assert str(_exact_number_match("0")) == "false"
    assert str(_exact_number_match("2147483648")) == "false"
    assert str(_exact_number_match("100000000000000000000")) == "false"
    # str.isdigit() accepts these; int() does not. Building the expression must not raise.
    assert str(_exact_number_match("²")) == "false"
    assert str(_exact_number_match("⑦")) == "false"


def test_admin_crud_round_trip_preserves_an_explicit_sort() -> None:
    """The omitted-sort sentinel must equal the list route's own default.

    ``problem_list_url`` drops the sort from generated URLs when it matches the
    default, and the list route re-derives that default from an absent value. If
    the two disagree, every CRUD redirect silently resets the admin's chosen sort.
    """

    class _StubRequest:
        def url_for(self, name: str) -> str:
            return "http://testserver/admin/problems"

    request = cast(Any, _StubRequest())

    assert _effective_problem_sort(None, "") == DEFAULT_SORT

    default_url = problem_list_url(request, sort_by=DEFAULT_SORT)
    assert "sort_by" not in default_url

    explicit_url = problem_list_url(request, sort_by="title_asc")
    assert "sort_by=title_asc" in explicit_url
    assert _effective_problem_sort("title_asc", "") == "title_asc"


def test_problem_search_migration_uses_transactional_expression_indexes() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")

    assert "CONCURRENTLY" not in source
    assert "ADD COLUMN" not in source
    assert "coalesce(author, '')" in source
    assert "(arena_number::text) gin_trgm_ops" in source
    assert source.count("CREATE INDEX") == 7


def test_problem_search_vectors_match_the_migration_expression() -> None:
    """Picker vectors must stay aligned with the indexed composite expression."""
    source = _MIGRATION.read_text(encoding="utf-8")
    indexed_expression = source.split("ON arena_problems USING gin ((", 1)[1].split("\n        ))", 1)[0]

    assert dedent(indexed_expression).strip() == _SEARCH_VECTOR_SQL.replace("arena_problems.", "")
    assert " ".join(_TITLE_SEARCH_VECTOR_SQL.split()) in " ".join(_SEARCH_VECTOR_SQL.split())
