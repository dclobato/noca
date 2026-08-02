#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""PostgreSQL integration coverage for Arena problem search."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

from arena.config import settings
from arena.database import create_engine
from arena.models.arena_problems import ArenaProblem
from arena.services.admin_problem_service import search_problem_suggestions
from arena.services.problem_browse_service import list_enabled_problems_paginated
from arena.services.problem_search_service import (
    ProblemSuggestionField,
    _postgres_search,
    _postgres_suggestion_search,
)
from shared.db_schema.arena import arena_problems, arena_users


@pytest_asyncio.fixture
async def postgres_search_session() -> AsyncIterator[AsyncSession]:
    """Yield a rolled-back session bound to a real PostgreSQL database.

    Follows the same "try, then skip" contract as the Valkey fixtures: the suite's
    default credentials point at a database that does not exist locally, so an
    unreachable server skips instead of failing. CI sets real ``NOCA_DB_*`` values
    and runs the migrations, so the test executes there.
    """
    engine = create_engine(settings.db_url, poolclass=NullPool)
    # Never interpolate settings.db_url into output: it carries the password in
    # clear text, and skip reasons reach CI logs and JUnit artifacts.
    safe_url = engine.url.render_as_string(hide_password=True)
    try:
        try:
            connection = await engine.connect()
        except Exception as exc:
            # Broad by design, as in the Valkey fixtures: the driver raises its own
            # unwrapped errors (bad password, missing database) alongside OSError.
            pytest.skip(f"PostgreSQL at {safe_url} is unavailable for tests: {exc}")
        try:
            transaction = await connection.begin()
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                yield session
            await transaction.rollback()
        finally:
            await connection.close()
    finally:
        await engine.dispose()


async def _search_titles(session: AsyncSession, query: str) -> list[str]:
    """Return public result titles in relevance order for one query."""
    page = await list_enabled_problems_paginated(
        session,
        page=1,
        per_page=25,
        search=query,
        sort_by="relevance",
    )
    return [problem.title for problem in page.items]


@pytest.mark.real_db
async def test_postgresql_search_behavior_and_int4_bounds(
    postgres_search_session: AsyncSession,
) -> None:
    """Exercise stemming, weighted rank, trigram matching, and numeric bounds on PostgreSQL."""
    session = postgres_search_session
    index_names = set(
        await session.scalars(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE indexname LIKE 'ix_arena_problems_%_trgm' "
                "OR indexname = 'ix_arena_problems_search_vector_gin'"
            )
        )
    )
    assert {
        "ix_arena_problems_search_vector_gin",
        "ix_arena_problems_number_text_trgm",
        "ix_arena_problems_title_trgm",
        "ix_arena_problems_statement_trgm",
        "ix_arena_problems_source_trgm",
        "ix_arena_problems_author_trgm",
    } <= index_names, "Arena problem-search migration is not applied to the PostgreSQL test database"

    owner_id = "00000000-0000-4000-8000-000000000901"
    await session.execute(
        insert(arena_users).values(
            id=owner_id,
            nome="Ada Lovelace",
            email_normalizado="problem-search-postgresql@noca.invalid",
            password_hash="unused",
            ativo=True,
        )
    )
    await session.execute(
        insert(arena_problems),
        [
            {
                "id": "00000000-0000-4000-8000-000000000911",
                "arena_number": 2_100_000_011,
                "title": "Running Algorithms",
                "owner_id": owner_id,
                "author": "Programadores Unidos",
                "author_is_owner": False,
                "source": None,
                "enabled": True,
                "problem_statement": "Compute the final value.",
                "statement_language": "pt",
            },
            {
                "id": "00000000-0000-4000-8000-000000000912",
                "arena_number": 2_100_000_012,
                "title": "Sequence Analysis",
                "owner_id": owner_id,
                "author": None,
                "author_is_owner": True,
                "source": None,
                "enabled": True,
                "problem_statement": "Running values must be accumulated.",
                "statement_language": "en",
            },
            {
                "id": "00000000-0000-4000-8000-000000000913",
                "arena_number": 2_100_000_013,
                "title": "Airplane Routes",
                "owner_id": owner_id,
                "author": None,
                "author_is_owner": True,
                "source": None,
                "enabled": True,
                "problem_statement": "Find the shortest route.",
                "statement_language": "en",
            },
            {
                "id": "00000000-0000-4000-8000-000000000914",
                "arena_number": 2_100_000_014,
                "title": "Hidden Vehicle",
                "owner_id": owner_id,
                "author": None,
                "author_is_owner": True,
                "source": None,
                "enabled": True,
                "problem_statement": "Track one intercontinentalairplane safely.",
                "statement_language": "en",
            },
            {
                "id": "00000000-0000-4000-8000-000000000915",
                "arena_number": 2_100_000_015,
                "title": "Misleading Needle Title",
                "owner_id": owner_id,
                "author": None,
                "author_is_owner": True,
                "source": "Unrelated archive",
                "enabled": False,
                "problem_statement": "This problem has the term only in its title.",
                "statement_language": "en",
            },
            {
                "id": "00000000-0000-4000-8000-000000000916",
                "arena_number": 2_100_000_016,
                "title": "Stored source value",
                "owner_id": owner_id,
                "author": None,
                "author_is_owner": True,
                "source": "Needle archive",
                "enabled": False,
                "problem_statement": "This problem stores the suggestion term in its source.",
                "statement_language": "en",
            },
        ],
    )
    await session.flush()

    assert (await _search_titles(session, "running"))[:2] == [
        "Running Algorithms",
        "Sequence Analysis",
    ]
    assert "Running Algorithms" in await _search_titles(session, "programador")
    assert "Airplane Routes" in await _search_titles(session, "Airplne")
    assert "Hidden Vehicle" in await _search_titles(session, "rplane")
    assert await _search_titles(session, "1_0") == []
    assert await _search_titles(session, "3000000000") == []
    assert await _search_titles(session, "100000000000000000000") == []
    # str.isdigit() accepts these but int() rejects them; searching must not raise.
    assert await _search_titles(session, "²") == []
    assert await _search_titles(session, "⑦") == []
    assert await search_problem_suggestions(
        session,
        field="source",
        query="needle",
        caller_id=owner_id,
        is_admin=True,
    ) == ["Needle archive"]

    await session.execute(
        text(
            """
            INSERT INTO arena_problems (
                id, arena_number, title, owner_id, author, author_is_owner,
                enabled, problem_statement, statement_language
            )
            SELECT
                md5('fts-plan-' || series::text),
                100000 + series,
                'Planner title ' || series,
                :owner_id,
                'Planner Author',
                false,
                true,
                CASE WHEN series = 2
                    THEN repeat('ordinary statement text ', 40) || ' statementmarker'
                    ELSE repeat('ordinary statement text ', 40)
                END,
                'en'::statementlanguage
            FROM generate_series(1, 5000) AS series
            """
        ),
        {"owner_id": owner_id},
    )
    await session.execute(text("ANALYZE arena_problems"))
    await session.execute(text("ANALYZE arena_users"))
    await session.execute(text("SET LOCAL enable_seqscan = off"))

    async def _plan_for(query: str) -> str:
        """Return the EXPLAIN output for one search predicate."""
        # Compile against the *live* dialect, not a standalone one. Only a connected
        # dialect knows the server has standard_conforming_strings on, and a
        # standalone instance therefore renders the ILIKE ESCAPE clause as a
        # two-character string that PostgreSQL rejects outright.
        search_sql = str(
            select(ArenaProblem.id)
            .where(_postgres_search(query).predicate)
            .compile(
                dialect=session.get_bind().dialect,
                compile_kwargs={"literal_binds": True},
            )
        )
        plan_rows = await session.execute(text(f"EXPLAIN (COSTS OFF) {search_sql}"))
        return "\n".join(str(row[0]) for row in plan_rows)

    async def _suggestion_plan_for(field: ProblemSuggestionField, query: str) -> str:
        """Return the EXPLAIN output for a field-specific suggestion predicate."""
        search_sql = str(
            select(ArenaProblem.id)
            .where(_postgres_suggestion_search(field, query).predicate)
            .compile(
                dialect=session.get_bind().dialect,
                compile_kwargs={"literal_binds": True},
            )
        )
        plan_rows = await session.execute(text(f"EXPLAIN (COSTS OFF) {search_sql}"))
        return "\n".join(str(row[0]) for row in plan_rows)

    # A quoted term is pure FTS: only the weighted expression index can serve it.
    quoted_plan = await _plan_for('"statementmarker"')
    assert "ix_arena_problems_search_vector_gin" in quoted_plan
    assert "Seq Scan on arena_problems" not in quoted_plan

    # A bare term additionally opens the substring and fuzzy branches. Each one is a
    # separate UNION arm precisely so it stays independently indexable.
    bare_plan = await _plan_for("statementmarker")
    assert "ix_arena_problems_search_vector_gin" in bare_plan
    assert "ix_arena_problems_statement_trgm" in bare_plan
    assert "Seq Scan on arena_problems" not in bare_plan

    source_plan = await _suggestion_plan_for("source", "Needle")
    assert "ix_arena_problems_search_vector_gin" in source_plan
    assert "ix_arena_problems_source_trgm" in source_plan
    assert "Seq Scan on arena_problems" not in source_plan

    author_plan = await _suggestion_plan_for("author", "Planner")
    assert "ix_arena_problems_search_vector_gin" in author_plan
    assert "ix_arena_problems_author_trgm" in author_plan
    assert "Seq Scan on arena_problems" not in author_plan
