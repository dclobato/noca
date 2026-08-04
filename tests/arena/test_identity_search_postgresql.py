#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""PostgreSQL integration coverage for Arena ranking search."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

from arena.config import settings
from arena.database import create_engine
from arena.services.arena_class_detail_service import search_student_autocomplete
from arena.services.arena_class_service import create_class
from arena.services.identity_search_service import (
    _postgres_affiliation_candidates,
    _postgres_user_candidates,
)
from arena.services.ranking_service import get_ranked_affiliations_paginated, get_ranked_users_paginated
from arena.services.text_search_primitives import apply_trigram_threshold
from shared.db_schema.arena import arena_affiliations, arena_users
from shared.enumerations import ArenaRole

_AFFILIATION_ID = "00000000-0000-4000-8000-000000000a01"
_ADA_ID = "00000000-0000-4000-8000-000000000a11"
_GRACE_ID = "00000000-0000-4000-8000-000000000a12"
_HIDDEN_ID = "00000000-0000-4000-8000-000000000a13"


@pytest_asyncio.fixture
async def postgres_ranking_session() -> AsyncIterator[AsyncSession]:
    """Yield a rolled-back session bound to a real PostgreSQL database.

    Follows the same "try, then skip" contract as the problem-search fixture: the
    suite's default credentials point at a database that does not exist locally,
    so an unreachable server skips instead of failing. CI sets real ``NOCA_DB_*``
    values and runs the migrations, so the test executes there.
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


async def _seed(session: AsyncSession) -> None:
    """Insert the named rows plus enough filler for the planner to prefer indexes.

    The filler volume is load-bearing. ``nome`` and ``name`` are short columns, so
    evaluating an ILIKE or ``%`` filter per row is cheap; below roughly ten
    thousand rows PostgreSQL prefers a full scan even with ``enable_seqscan``
    off, and the plan assertions would fail while the indexes are perfectly
    usable. These counts keep the cost difference decisive.
    """
    await session.execute(
        insert(arena_affiliations).values(
            id=_AFFILIATION_ID,
            name="Universidade Federal do Ceara",
            rating=200,
            exclude_from_ranking=False,
        )
    )
    await session.execute(
        insert(arena_users),
        [
            {
                "id": _ADA_ID,
                "nome": "Ada Lovelace",
                "email_normalizado": "ada.lovelace@ranking-search.invalid",
                "password_hash": "unused",
                "ativo": True,
                "email_confirmado": True,
                "ranking_visible": True,
                "affiliation_id": _AFFILIATION_ID,
                "user_rating": 300,
                "solved_problems": 5,
            },
            {
                "id": _GRACE_ID,
                "nome": "Grace Hopper",
                "email_normalizado": "grace.hopper@ranking-search.invalid",
                "password_hash": "unused",
                "ativo": True,
                "email_confirmado": True,
                "ranking_visible": True,
                "user_rating": 500,
                "solved_problems": 9,
                "affiliation_id": None,
            },
            {
                # The candidate query deliberately omits the eligibility filters,
                # so this row is matched by the search and must be discarded by
                # the ranked CTE instead.
                "id": _HIDDEN_ID,
                "nome": "Ada Lovelace Hidden",
                "email_normalizado": "hidden.lovelace@ranking-search.invalid",
                "password_hash": "unused",
                "ativo": True,
                "email_confirmado": True,
                "ranking_visible": False,
                "user_rating": 999,
                "solved_problems": 9,
                "affiliation_id": None,
            },
        ],
    )
    await session.execute(
        text(
            """
            INSERT INTO arena_users (
                id, nome, email_normalizado, password_hash, ativo,
                email_confirmado, ranking_visible, user_rating, solved_problems
            )
            SELECT
                md5('rank-user-' || series::text),
                'Filler Name ' || series,
                'filler-' || series || '@ranking-seed.invalid',
                'unused',
                true, true, true, series % 500, 1
            FROM generate_series(1, 50000) AS series
            """
        )
    )
    await session.execute(
        text(
            """
            INSERT INTO arena_affiliations (id, name, rating, exclude_from_ranking)
            SELECT
                md5('rank-affiliation-' || series::text),
                'Filler Affiliation ' || series,
                series % 300,
                false
            FROM generate_series(1, 20000) AS series
            """
        )
    )
    await session.execute(text("ANALYZE arena_users"))
    await session.execute(text("ANALYZE arena_affiliations"))


async def _user_names(session: AsyncSession, query: str) -> list[str]:
    """Return the ranked user names matching one search query."""
    page = await get_ranked_users_paginated(session, search=query, per_page=25)
    return [item.name for item in page.items]


async def _assert_student_autocomplete_ranks_the_typed_name_first(session: AsyncSession) -> None:
    """The class-membership autocomplete must survive its own truncation.

    Fuzzy matching means a typo now returns candidates that share no literal
    substring with the query. The endpoint returns only ten rows, so ordering by
    name — as it did before — could leave the intended student off the list
    entirely. This asserts the relevance ordering instead puts them first.
    """
    teacher_id = "00000000-0000-4000-8000-000000000a21"
    decoy_id = "00000000-0000-4000-8000-000000000a22"
    await session.execute(
        insert(arena_users).values(
            id=teacher_id,
            nome="Class Teacher",
            email_normalizado="teacher@ranking-search.invalid",
            password_hash="unused",
            ativo=True,
            email_confirmado=True,
            role=ArenaRole.ARENA_JUDGE.value,
        )
    )
    # Sorts before "Ada Lovelace" alphabetically and is only a fuzzy match, so
    # under the previous name-ordered query it would take the first slot.
    await session.execute(
        insert(arena_users).values(
            id=decoy_id,
            nome="Aaa Lovelacz",
            email_normalizado="decoy@ranking-search.invalid",
            password_hash="unused",
            ativo=True,
            email_confirmado=True,
            role=ArenaRole.ARENA_USER.value,
        )
    )
    arena_class = await create_class(
        session,
        actor_id=teacher_id,
        actor_role=ArenaRole.ARENA_JUDGE,
        name="Autocomplete Class",
        starts_on=date.today(),
        finishes_on=date.today() + timedelta(days=30),
        allow_self_registration=False,
    )

    rows = await search_student_autocomplete(
        session,
        actor_id=teacher_id,
        actor_role=ArenaRole.ARENA_JUDGE,
        class_id=arena_class.id,
        query="Lovelce",
    )
    labels = [row.label for row in rows]
    assert labels, "the typo should still return candidates"
    assert labels[0].startswith("Ada Lovelace <"), labels[:3]

    # A literal substring hit outranks fuzzy noise even when the noise sorts
    # earlier alphabetically. The decoy must still be *present* — otherwise this
    # would pass for the wrong reason, by the decoy simply not matching at all.
    literal_labels = [
        row.label
        for row in await search_student_autocomplete(
            session,
            actor_id=teacher_id,
            actor_role=ArenaRole.ARENA_JUDGE,
            class_id=arena_class.id,
            query="lovelace",
        )
    ]
    assert literal_labels[0].startswith("Ada Lovelace <"), literal_labels[:3]
    assert any(label.startswith("Aaa Lovelacz <") for label in literal_labels), literal_labels


@pytest.mark.real_db
async def test_postgresql_ranking_search_behavior_and_index_usage(
    postgres_ranking_session: AsyncSession,
) -> None:
    """Exercise full-text, substring, and fuzzy ranking search against real indexes."""
    session = postgres_ranking_session
    index_names = set(
        await session.scalars(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE indexname IN ("
                "'ix_arena_users_nome_fts_gin', 'ix_arena_users_nome_trgm', "
                "'ix_arena_users_email_normalizado_trgm', "
                "'ix_arena_affiliations_name_fts_gin', 'ix_arena_affiliations_name_trgm')"
            )
        )
    )
    assert {
        "ix_arena_users_nome_fts_gin",
        "ix_arena_users_nome_trgm",
        "ix_arena_users_email_normalizado_trgm",
        "ix_arena_affiliations_name_fts_gin",
        "ix_arena_affiliations_name_trgm",
    } <= index_names, "Arena ranking-search migration is not applied to the PostgreSQL test database"

    await _seed(session)

    # Substring, full-text, fuzzy, and email branches each find Ada on their own.
    assert "Ada Lovelace" in await _user_names(session, "lovel")
    assert "Ada Lovelace" in await _user_names(session, "Lovelace")
    assert "Ada Lovelace" in await _user_names(session, "Lovelce")
    assert "Ada Lovelace" in await _user_names(session, "ada.lovelace@")

    # A quoted phrase is pure FTS and must not drag in the other seeded user.
    phrase_names = await _user_names(session, '"grace hopper"')
    assert "Grace Hopper" in phrase_names
    assert "Ada Lovelace" not in phrase_names

    # A typed "%" is escaped for the substring branch rather than acting as a SQL
    # wildcard: the old lower(nome) LIKE '%50%%' matched every name containing
    # "50", so "Filler Name 500" is the row that proves the difference. The FTS
    # branch still tokenizes the query to the term "50" and matches that name
    # exactly, which is why this is not an empty result.
    wildcard_names = await _user_names(session, "50%")
    assert "Filler Name 50" in wildcard_names
    assert "Filler Name 500" not in wildcard_names

    affiliations = await get_ranked_affiliations_paginated(session, search="Univrsidade", per_page=25)
    assert "Universidade Federal do Ceara" in [item.name for item in affiliations.items]

    # Search filters only, it never renumbers: the rank shown for a searched user
    # must still be its global rank. Derive the expectation from the definition of
    # RANK() rather than from page 1, so pre-existing rows in the target database
    # cannot influence the result.
    higher_rated = await session.scalar(
        select(func.count())
        .select_from(arena_users)
        .where(
            arena_users.c.ativo.is_(True),
            arena_users.c.email_confirmado.is_(True),
            arena_users.c.ranking_visible.is_(True),
            func.coalesce(arena_users.c.user_rating, 0) > 500,
        )
    )
    searched = await get_ranked_users_paginated(session, search="Hopper", per_page=25)
    grace = next(item for item in searched.items if item.name == "Grace Hopper")
    assert grace.rank == (higher_rated or 0) + 1

    await _assert_student_autocomplete_ranks_the_typed_name_first(session)

    await apply_trigram_threshold(session)
    await session.execute(text("SET LOCAL enable_seqscan = off"))

    async def _plan_for(candidates: object) -> str:
        """Return the EXPLAIN output for one candidate-ID selectable."""
        # Compile against the *live* dialect, not a standalone one. Only a connected
        # dialect knows the server has standard_conforming_strings on, and a
        # standalone instance therefore renders the ILIKE ESCAPE clause as a
        # two-character string that PostgreSQL rejects outright.
        candidate_sql = str(
            candidates.compile(  # type: ignore[attr-defined]
                dialect=session.get_bind().dialect,
                compile_kwargs={"literal_binds": True},
            )
        )
        plan_rows = await session.execute(text(f"EXPLAIN (COSTS OFF) {candidate_sql}"))
        return "\n".join(str(row[0]) for row in plan_rows)

    # Every probe term is at least three characters: gin_trgm_ops can only serve an
    # ILIKE '%x%' pattern when the pattern yields a full trigram.
    bare_user_plan = await _plan_for(_postgres_user_candidates("Lovelace"))
    assert "ix_arena_users_nome_fts_gin" in bare_user_plan
    assert "ix_arena_users_nome_trgm" in bare_user_plan
    assert "ix_arena_users_email_normalizado_trgm" in bare_user_plan
    assert "Seq Scan on arena_users" not in bare_user_plan

    # A quoted term is pure FTS: only the expression index can serve it.
    quoted_user_plan = await _plan_for(_postgres_user_candidates('"Lovelace"'))
    assert "ix_arena_users_nome_fts_gin" in quoted_user_plan
    assert "Seq Scan on arena_users" not in quoted_user_plan

    affiliation_plan = await _plan_for(_postgres_affiliation_candidates("Universidade"))
    assert "ix_arena_affiliations_name_fts_gin" in affiliation_plan
    assert "ix_arena_affiliations_name_trgm" in affiliation_plan
    assert "Seq Scan on arena_affiliations" not in affiliation_plan

    # The candidate query matches the hidden user by name; only the ranked CTE's
    # eligibility filter keeps it out of the results.
    assert _HIDDEN_ID in set(await session.scalars(select(_postgres_user_candidates("Lovelace").subquery().c.id)))
    assert "Ada Lovelace Hidden" not in await _user_names(session, "Lovelace")
