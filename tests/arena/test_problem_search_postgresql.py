#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""PostgreSQL integration coverage for Arena problem search."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

from arena.config import settings
from arena.database import create_engine
from arena.models.arena_problems import ArenaProblem
from arena.services.admin_problem_service import search_problem_suggestions
from arena.services.arena_problem_set_management_service import search_set_candidate_problems
from arena.services.problem_browse_service import list_enabled_problems_paginated
from arena.services.problem_search_service import (
    ProblemSuggestionField,
    _postgres_problem_picker_search,
    _postgres_search,
    _postgres_suggestion_search,
    prepare_problem_search,
)
from shared.db_schema.arena import (
    arena_classes,
    arena_problem_sets,
    arena_problems,
    arena_users,
)
from shared.enumerations import ArenaRole, ProblemValidatorType


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


async def _seeded_search_titles(session: AsyncSession, query: str, owner_id: str) -> list[str]:
    """Return matching titles owned by this test's seeded user only."""
    expressions = await prepare_problem_search(session, query)
    return list(
        await session.scalars(
            select(ArenaProblem.title).where(
                ArenaProblem.owner_id == owner_id,
                expressions.predicate,
            )
        )
    )


async def _assert_problem_picker_behavior(session: AsyncSession, owner_id: str) -> None:
    """Exercise the problem-set autocomplete call site against PostgreSQL."""
    class_id = "00000000-0000-4000-8000-000000000902"
    set_id = "00000000-0000-4000-8000-000000000903"
    today = date.today()
    await session.execute(
        insert(arena_classes).values(
            id=class_id,
            name="PostgreSQL Picker Class",
            teacher_id=owner_id,
            starts_on=today,
            finishes_on=today,
        )
    )
    await session.execute(
        insert(arena_problem_sets).values(
            id=set_id,
            class_id=class_id,
            name="PostgreSQL Picker Set",
        )
    )

    blank_rows = await search_set_candidate_problems(
        session,
        actor_id=owner_id,
        actor_role=ArenaRole.ARENA_JUDGE,
        set_id=set_id,
        query="",
        limit=1,
    )
    wildcard_rows = await search_set_candidate_problems(
        session,
        actor_id=owner_id,
        actor_role=ArenaRole.ARENA_JUDGE,
        set_id=set_id,
        query="%",
    )
    typo_rows = await search_set_candidate_problems(
        session,
        actor_id=owner_id,
        actor_role=ArenaRole.ARENA_JUDGE,
        set_id=set_id,
        query="Airplne",
        limit=1,
    )
    number_rows = await search_set_candidate_problems(
        session,
        actor_id=owner_id,
        actor_role=ArenaRole.ARENA_JUDGE,
        set_id=set_id,
        query="2100000013",
        limit=1,
    )

    assert blank_rows
    assert wildcard_rows
    assert wildcard_rows[0].title == "%"
    assert all("%" in row.title for row in wildcard_rows)
    assert [row.title for row in typo_rows] == ["Airplane Routes"]
    assert [row.arena_number for row in number_rows] == [2_100_000_013]


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
                "OR indexname IN ("
                "'ix_arena_problems_search_vector_gin', "
                "'ix_arena_problems_license_search_vector_gin'"
                ")"
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
        "ix_arena_problems_license_search_vector_gin",
        "ix_arena_problems_license_trgm",
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
                "title": "Running Algorithms Searchfixturemarker",
                "validator_type": ProblemValidatorType.STANDARD,
                "owner_id": owner_id,
                "author": "Programadores Unidos",
                "author_is_owner": False,
                "source": None,
                "license": None,
                "enabled": True,
                "problem_statement": "Compute the final value.",
                "statement_language": "pt",
            },
            {
                "id": "00000000-0000-4000-8000-000000000912",
                "arena_number": 2_100_000_012,
                "title": "Sequence Analysis",
                "validator_type": ProblemValidatorType.STANDARD,
                "owner_id": owner_id,
                "author": None,
                "author_is_owner": True,
                "source": None,
                "license": None,
                "enabled": True,
                "problem_statement": "Running searchfixturemarker values must be accumulated.",
                "statement_language": "en",
            },
            {
                "id": "00000000-0000-4000-8000-000000000913",
                "arena_number": 2_100_000_013,
                "title": "Airplane Routes",
                "validator_type": ProblemValidatorType.STANDARD,
                "owner_id": owner_id,
                "author": None,
                "author_is_owner": True,
                "source": None,
                "license": None,
                "enabled": True,
                "problem_statement": "Find the shortest route.",
                "statement_language": "en",
            },
            {
                "id": "00000000-0000-4000-8000-000000000914",
                "arena_number": 2_100_000_014,
                "title": "Hidden Vehicle",
                "validator_type": ProblemValidatorType.STANDARD,
                "owner_id": owner_id,
                "author": None,
                "author_is_owner": True,
                "source": None,
                "license": None,
                "enabled": True,
                "problem_statement": "Track one intercontinentalairplane safely.",
                "statement_language": "en",
            },
            {
                "id": "00000000-0000-4000-8000-000000000915",
                "arena_number": 2_100_000_015,
                "title": "Misleading Needle Title",
                "validator_type": ProblemValidatorType.STANDARD,
                "owner_id": owner_id,
                "author": None,
                "author_is_owner": True,
                "source": "Unrelated archive",
                "license": None,
                "enabled": False,
                "problem_statement": "This problem has the term only in its title.",
                "statement_language": "en",
            },
            {
                "id": "00000000-0000-4000-8000-000000000916",
                "arena_number": 2_100_000_016,
                "title": "Stored source value",
                "validator_type": ProblemValidatorType.STANDARD,
                "owner_id": owner_id,
                "author": None,
                "author_is_owner": True,
                "source": "Needle archive Searchfixturemarker",
                "license": "Creative Commons Attribution 4.0",
                "enabled": False,
                "problem_statement": "This problem stores the suggestion term in its source.",
                "statement_language": "en",
            },
            {
                "id": "00000000-0000-4000-8000-000000000917",
                "arena_number": 2_100_000_017,
                "title": "%",
                "validator_type": ProblemValidatorType.STANDARD,
                "owner_id": owner_id,
                "author": None,
                "author_is_owner": True,
                "source": None,
                "license": None,
                "enabled": True,
                "problem_statement": "Literal percent wildcard fixture.",
                "statement_language": "en",
            },
        ],
    )
    await session.flush()

    assert (await _search_titles(session, "running searchfixturemarker"))[:2] == [
        "Running Algorithms Searchfixturemarker",
        "Sequence Analysis",
    ]
    assert "Running Algorithms Searchfixturemarker" in await _search_titles(session, "programador")
    assert "Airplane Routes" in await _search_titles(session, "Airplne")
    assert "Hidden Vehicle" in await _search_titles(session, "rplane")
    assert await _seeded_search_titles(session, "1_0", owner_id) == []
    assert await _seeded_search_titles(session, "3000000000", owner_id) == []
    assert await _seeded_search_titles(session, "100000000000000000000", owner_id) == []
    # str.isdigit() accepts these but int() rejects them; searching must not raise.
    assert await _seeded_search_titles(session, "²", owner_id) == []
    assert await _seeded_search_titles(session, "⑦", owner_id) == []
    assert await search_problem_suggestions(
        session,
        field="source",
        query="needle searchfixturemarker",
        caller_id=owner_id,
        is_admin=True,
    ) == ["Needle archive Searchfixturemarker"]
    assert await search_problem_suggestions(
        session,
        field="license",
        query="creative attribution",
        caller_id=owner_id,
        is_admin=True,
    ) == ["Creative Commons Attribution 4.0"]

    async def _source_suggestions(query: str) -> list[str]:
        """Search stored sources as the fixture owner."""
        return await search_problem_suggestions(
            session,
            field="source",
            query=query,
            caller_id=owner_id,
            is_admin=True,
        )

    # Each term matches as its own substring, so a term the user has not
    # finished typing still matches -- which neither whole-lexeme full-text
    # matching nor a contiguous whole-query substring can do. "Needle archive
    # Searchfixturemarker" is matched here by partial terms, by terms in the
    # wrong order, and by terms that begin mid-word.
    assert await _source_suggestions("needle searchfixturemark") == ["Needle archive Searchfixturemarker"]
    assert await _source_suggestions("searchfixturemarker archiv") == ["Needle archive Searchfixturemarker"]
    assert await _source_suggestions("earchfixturemarker eedle") == ["Needle archive Searchfixturemarker"]
    # The terms are AND-ed, so adding one that matches nothing excludes a row
    # the first term alone returns. The demonstration uses an absent term rather
    # than one belonging to a *different* row, because the branches are a UNION:
    # per-term substring matching can only add candidates, and the whole-query
    # trigram branch keeps its own reach (similarity('Unrelated archive',
    # 'needle unrelated') is 0.40, over the 0.3 threshold).
    assert await _source_suggestions("needle") == ["Needle archive Searchfixturemarker"]
    assert await _source_suggestions("needle zzzabsentterm") == []
    assert await search_problem_suggestions(
        session,
        field="author",
        query="programadores unid",
        caller_id=owner_id,
        is_admin=True,
    ) == ["Programadores Unidos"]
    assert await search_problem_suggestions(
        session,
        field="license",
        query="creative attribu",
        caller_id=owner_id,
        is_admin=True,
    ) == ["Creative Commons Attribution 4.0"]
    await _assert_problem_picker_behavior(session, owner_id)

    # Every optional column the suggestion predicates touch is populated on the
    # whole corpus, and that is what makes the plan assertions below stable.
    # `ix_arena_problems_source_trgm`, `ix_arena_problems_author_trgm`,
    # `ix_arena_problems_license_trgm` and
    # `ix_arena_problems_license_search_vector_gin` are all *partial* indexes on
    # `<column> IS NOT NULL`. Leaving `source` and `license` NULL across the
    # corpus made that bare NOT NULL test the most selective thing in the
    # predicate, so the planner served an entire branch by scanning one of those
    # partial indexes end to end and rechecking everything else as a filter --
    # a plan that names neither the trigram index the branch is supposed to use
    # nor the full-text index the FTS branch is supposed to use, and whose choice
    # flipped between the two on nothing more than the table's page count. With
    # the columns populated the NOT NULL test matches every row and each branch
    # is planned on the index it actually needs. `author` was already populated,
    # which is exactly why its assertion never failed.
    await session.execute(
        text(
            """
            INSERT INTO arena_problems (
                id, arena_number, title, owner_id, author, author_is_owner,
                enabled, problem_statement, source, license,
                statement_language, validator_type
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
                'Planner catalogue volume ' || series,
                'Planner Public License ' || series,
                'en'::statementlanguage,
                'standard'::problemvalidatortype
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

    async def _picker_plan_for(query: str) -> str:
        """Return the EXPLAIN output for the title-and-number picker predicate."""
        search_sql = str(
            select(ArenaProblem.id)
            .where(_postgres_problem_picker_search(query).predicate)
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

    # Per-term substring matching stays indexable: pg_trgm drives the branch
    # with the terms long enough to yield trigrams and rechecks the rest.
    # EXPLAIN renders ILIKE as the `~~*` operator, so that is what names the
    # branch in a plan.
    multi_term_plan = await _suggestion_plan_for("source", "Needle archiv")
    assert "~~*" in multi_term_plan
    assert "ix_arena_problems_source_trgm" in multi_term_plan
    assert "Seq Scan on arena_problems" not in multi_term_plan

    # A term that cannot yield a trigram opens neither trigram-served branch,
    # so neither becomes a sequential scan. Character count is not the test:
    # `%---%`, `%²²²%`, and `%ga%` were each confirmed against this server to
    # plan as a sequential scan, while `%gam%` and `%a-bc%` plan as bitmap index
    # scans. `~~*` is EXPLAIN's rendering of ILIKE and `%` is the pg_trgm
    # similarity operator the fuzzy branch uses.
    for declined in ("a b", "---", "²²²", "ga"):
        declined_plan = await _suggestion_plan_for("source", declined)
        assert "~~*" not in declined_plan, declined
        assert "Seq Scan on arena_problems" not in declined_plan, declined
    punctuation_plan = await _suggestion_plan_for("source", "---")
    assert "% '---'" not in punctuation_plan

    author_plan = await _suggestion_plan_for("author", "Planner")
    assert "ix_arena_problems_search_vector_gin" in author_plan
    assert "ix_arena_problems_author_trgm" in author_plan
    assert "Seq Scan on arena_problems" not in author_plan

    license_plan = await _suggestion_plan_for("license", "Creative")
    assert "ix_arena_problems_license_search_vector_gin" in license_plan
    assert "ix_arena_problems_license_trgm" in license_plan
    assert "Seq Scan on arena_problems" not in license_plan

    picker_plan = await _picker_plan_for("Planner")
    assert "ix_arena_problems_search_vector_gin" in picker_plan
    assert "ix_arena_problems_number_text_trgm" in picker_plan
    assert "ix_arena_problems_title_trgm" in picker_plan
    assert "Seq Scan on arena_problems" not in picker_plan
