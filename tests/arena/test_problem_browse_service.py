#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for public Arena problem browsing."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.models.arena_problems import (
    ArenaCategory,
    ArenaCollection,
    ArenaProblem,
    ArenaProblemCustomValidator,
)
from arena.models.arena_users import ArenaUser
from arena.services.problem_browse_service import (
    get_latest_problems,
    list_collections_with_counts,
    list_enabled_problems_paginated,
)
from shared.db_schema.arena import arena_problem_category_map, arena_problem_ratings, arena_problem_solvers
from shared.enumerations import (
    ArenaRole,
    CustomValidatorActiveState,
    CustomValidatorCandidateState,
    ProblemValidatorType,
)
from web.models.language import Language

_TEMPLATE = Path(__file__).resolve().parents[2] / "arena" / "template" / "problems" / "problem_list.html"


async def _make_user(session: AsyncSession, *, role: ArenaRole) -> ArenaUser:
    user = ArenaUser(
        nome=f"Browse {role.value}",
        email_normalizado=f"browse-{role.value}-{uuid.uuid4().hex[:8]}@test.example.com",
        dta_nascimento=date(1998, 1, 1),
        role=role,
    )
    user.password = "Senha@Forte1!"
    session.add(user)
    await session.flush()
    return user


async def _make_language(session: AsyncSession) -> Language:
    language = Language(
        id=f"browse-test-{uuid.uuid4().hex[:8]}",
        name="Browse Test Language",
        icon="test",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="main.txt",
        artifact_path="/sandbox/main.txt",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(language)
    await session.flush()
    return language


async def _make_problem(
    session: AsyncSession,
    owner: ArenaUser,
    *,
    arena_number: int | None = None,
    title: str | None = None,
    author: str | None = None,
    source: str | None = None,
    statement: str = "<p>Echo.</p>",
    validator_type: ProblemValidatorType = ProblemValidatorType.STANDARD,
) -> ArenaProblem:
    problem = ArenaProblem(
        arena_number=arena_number or int(uuid.uuid4().int % 1_000_000_000) + 1,
        title=title or f"Browse Problem {uuid.uuid4().hex[:8]}",
        owner_id=owner.id,
        author=author,
        author_is_owner=author is None,
        source=source,
        enabled=True,
        problem_statement=statement,
        validator_type=validator_type,
    )
    session.add(problem)
    await session.flush()
    return problem


async def _record_solver(
    session: AsyncSession,
    *,
    problem: ArenaProblem,
    user: ArenaUser,
) -> None:
    await session.execute(
        arena_problem_solvers.insert().values(
            problem_id=problem.id,
            user_id=user.id,
            solved_at=datetime.now(UTC),
        )
    )


@pytest.mark.asyncio
async def test_public_problem_list_excludes_owner_from_aggregate_stats(
    session: AsyncSession,
) -> None:
    """Aggregate solver count excludes only the problem owner; all roles count.

    A counted user's solve is reflected in the aggregate, while the excluded
    owner's own solved marker still surfaces as a personal label.
    """
    author = await _make_user(session, role=ArenaRole.ARENA_USER)
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    admin = await _make_user(session, role=ArenaRole.ARENA_ADMIN)
    problem = await _make_problem(session, author)

    # Counted: judge and admin solves. Excluded: the owner's own solve.
    await _record_solver(session, problem=problem, user=judge)
    await _record_solver(session, problem=problem, user=admin)
    await _record_solver(session, problem=problem, user=author)
    await session.execute(
        arena_problem_ratings.insert().values(
            problem_id=problem.id,
            attempted_users=0,
            solved_users=0,
            rating=50,
        )
    )
    await session.flush()

    # The author (owner) views the list: their own solve is excluded from the
    # aggregate but the personal solved marker remains.
    pagination = await list_enabled_problems_paginated(
        session,
        page=1,
        user_id=author.id,
    )

    assert pagination.total == 1
    item = pagination.items[0]
    assert item.id == problem.id
    assert item.difficulty.state == "unknown"
    assert item.difficulty.value is None
    assert item.solved == 2  # judge and admin count; only the owner is excluded
    assert item.ac_rate == 0.0
    assert item.is_solved is True


@pytest.mark.asyncio
async def test_public_problem_list_resolves_owner_and_free_text_authors(
    session: AsyncSession,
) -> None:
    owner = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    owner_problem = await _make_problem(session, owner, arena_number=401, title="Owner Work")
    external_problem = await _make_problem(
        session,
        owner,
        arena_number=402,
        title="External Work",
        author="Guest Writer",
    )
    await session.flush()

    pagination = await list_enabled_problems_paginated(session, page=1)
    authors_by_problem = {item.id: item.author_name for item in pagination.items}
    assert authors_by_problem[owner_problem.id] == owner.nome
    assert authors_by_problem[external_problem.id] == "Guest Writer"
    assert all(item.difficulty.value is None and item.ac_rate is None for item in pagination.items)

    external_search = await list_enabled_problems_paginated(
        session,
        page=1,
        search="Guest Writer",
    )
    assert [item.id for item in external_search.items] == [external_problem.id]


@pytest.mark.asyncio
async def test_public_problem_list_searches_statement_source_and_literal_wildcards(
    session: AsyncSession,
) -> None:
    owner = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    statement_problem = await _make_problem(
        session,
        owner,
        arena_number=411,
        title="Ordinary title",
        statement="Find the midword-airplane route.",
    )
    source_problem = await _make_problem(
        session,
        owner,
        arena_number=412,
        title="Another title",
        source="Regional 50%_off Cup",
    )

    statement_search = await list_enabled_problems_paginated(session, page=1, search="rplane")
    literal_search = await list_enabled_problems_paginated(session, page=1, search="50%_off")

    assert [item.id for item in statement_search.items] == [statement_problem.id]
    assert [item.id for item in literal_search.items] == [source_problem.id]


@pytest.mark.asyncio
async def test_public_problem_list_filters_categories_with_or_semantics(
    session: AsyncSession,
) -> None:
    """Selecting multiple categories returns problems linked to any of them."""
    owner = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    graphs = ArenaCategory(name="Graphs", slug="graphs")
    dynamic_programming = ArenaCategory(name="Dynamic Programming", slug="dp")
    session.add_all([graphs, dynamic_programming])
    both = await _make_problem(session, owner, arena_number=421, title="Both Categories")
    graphs_only = await _make_problem(session, owner, arena_number=422, title="Graphs Only")
    dp_only = await _make_problem(session, owner, arena_number=423, title="DP Only")
    await _make_problem(session, owner, arena_number=424, title="Uncategorized")
    await session.flush()
    await session.execute(
        arena_problem_category_map.insert(),
        [
            {"problem_id": both.id, "category_id": graphs.id},
            {"problem_id": both.id, "category_id": dynamic_programming.id},
            {"problem_id": graphs_only.id, "category_id": graphs.id},
            {"problem_id": dp_only.id, "category_id": dynamic_programming.id},
        ],
    )
    await session.flush()

    pagination = await list_enabled_problems_paginated(
        session,
        page=1,
        category_slugs=[graphs.slug, dynamic_programming.slug],
    )

    assert [item.arena_number for item in pagination.items] == [421, 422, 423]
    assert pagination.total == 3
    assert len({item.id for item in pagination.items}) == 3


@pytest.mark.asyncio
async def test_public_problem_list_sorts_by_user_solvers_descending(
    session: AsyncSession,
) -> None:
    """Solver sorting counts every role and only excludes the problem owner."""
    author = await _make_user(session, role=ArenaRole.ARENA_USER)
    users = [await _make_user(session, role=ArenaRole.ARENA_USER) for _ in range(3)]
    admin = await _make_user(session, role=ArenaRole.ARENA_ADMIN)
    one_solver = await _make_problem(session, author, arena_number=101, title="One")
    two_solvers = await _make_problem(session, author, arena_number=102, title="Two")
    staff_only = await _make_problem(session, author, arena_number=103, title="Staff")

    await _record_solver(session, problem=one_solver, user=users[0])
    await _record_solver(session, problem=two_solvers, user=users[1])
    await _record_solver(session, problem=two_solvers, user=users[2])
    await _record_solver(session, problem=staff_only, user=admin)
    await session.flush()

    pagination = await list_enabled_problems_paginated(
        session,
        page=1,
        sort_by="solvers_desc",
    )

    # two_solvers (2) ranks first; one_solver and staff_only tie at 1 and break
    # by arena_number ascending. The admin solve on staff_only now counts.
    assert [item.arena_number for item in pagination.items] == [102, 101, 103]
    assert [item.solved for item in pagination.items] == [2, 1, 1]


@pytest.mark.asyncio
async def test_public_problem_list_sorts_by_user_solvers_ascending_with_number_tiebreaker(
    session: AsyncSession,
) -> None:
    """Ascending solver sort treats no solvers as zero and falls back to problem number."""
    author = await _make_user(session, role=ArenaRole.ARENA_USER)
    solver = await _make_user(session, role=ArenaRole.ARENA_USER)
    await _make_problem(session, author, arena_number=303, title="Zero High")
    one_solver = await _make_problem(session, author, arena_number=301, title="One")
    await _make_problem(session, author, arena_number=302, title="Zero Low")

    await _record_solver(session, problem=one_solver, user=solver)
    await session.flush()

    pagination = await list_enabled_problems_paginated(
        session,
        page=1,
        sort_by="solvers_asc",
    )

    assert [item.arena_number for item in pagination.items] == [302, 303, 301]
    assert [item.solved for item in pagination.items] == [None, None, 1]


@pytest.mark.asyncio
async def test_public_problem_list_marks_custom_validator_problems(
    session: AsyncSession,
) -> None:
    owner = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    language = await _make_language(session)
    plain_problem = await _make_problem(session, owner, arena_number=501, title="Plain")
    validator_problem = await _make_problem(
        session,
        owner,
        arena_number=502,
        title="Interactive",
        validator_type=ProblemValidatorType.INTERACTIVE,
    )
    candidate_problem = await _make_problem(
        session,
        owner,
        arena_number=503,
        title="Candidate",
        validator_type=ProblemValidatorType.INTERACTIVE,
    )
    session.add(
        ArenaProblemCustomValidator(
            problem_id=validator_problem.id,
            active_language_id=language.id,
            active_source="print('validator')\n",
            active_state=CustomValidatorActiveState.VALID,
            active_validated_at=datetime.now(UTC),
        )
    )
    session.add(
        ArenaProblemCustomValidator(
            problem_id=candidate_problem.id,
            candidate_language_id=language.id,
            candidate_source="print('candidate')\n",
            candidate_token=str(uuid.uuid4()),
            candidate_state=CustomValidatorCandidateState.PENDING,
        )
    )
    await session.flush()

    pagination = await list_enabled_problems_paginated(session, page=1)

    flags_by_problem = {item.id: item.has_custom_validator for item in pagination.items}
    assert flags_by_problem[plain_problem.id] is False
    assert flags_by_problem[validator_problem.id] is True
    assert flags_by_problem[candidate_problem.id] is True


@pytest.mark.asyncio
async def test_public_problem_list_uses_narrow_bounded_queries(
    session: AsyncSession,
    sql_statements: list[str],
) -> None:
    """Public list projections stay narrow and never require row deduplication."""
    owner = await _make_user(session, role=ArenaRole.ARENA_USER)
    problem = await _make_problem(session, owner, arena_number=601, title="Narrow")
    problem.problem_image_base64 = "large-image-payload"
    problem.problem_image_mime = "image/png"
    problem.problem_image_caption = "Caption"
    problem.notes = "Internal note"
    problem.license = "CC BY 4.0"
    await session.flush()

    sql_statements.clear()
    guest_page = await list_enabled_problems_paginated(session, page=1)

    # Count, page, then one bounded IN-query per enrichment: categories and
    # collections. A guest needs no favorite/solved lookups.
    assert len(sql_statements) == 4
    count_sql, page_sql, category_sql, collection_sql = [statement.lower() for statement in sql_statements]
    assert "arena_problem_ratings" not in count_sql
    assert "arena_problem_solvers" not in count_sql
    assert "arena_problem_custom_validators" not in count_sql
    for large_column in (
        "problem_statement",
        "problem_image_base64",
        "problem_image_caption",
        "notes",
        "license",
    ):
        assert large_column not in page_sql
    assert "arena_problem_custom_validators.active_source," not in page_sql
    assert "arena_problem_custom_validators.candidate_source," not in page_sql
    assert " in (" in category_sql
    # Page-scoped, not one query per row.
    assert " in (" in collection_sql
    assert [item.id for item in guest_page.items] == [problem.id]
    assert len({item.id for item in guest_page.items}) == len(guest_page.items)

    sql_statements.clear()
    await list_enabled_problems_paginated(session, page=1, user_id=owner.id)
    assert len(sql_statements) == 6


@pytest.mark.asyncio
async def test_latest_problems_selects_only_dashboard_fields(
    session: AsyncSession,
    sql_statements: list[str],
) -> None:
    """Latest-problems uses one query with only its three projected columns."""
    owner = await _make_user(session, role=ArenaRole.ARENA_USER)
    older = await _make_problem(session, owner, arena_number=701, title="Older")
    newer = await _make_problem(session, owner, arena_number=702, title="Newer")
    older.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    newer.updated_at = datetime(2026, 2, 1, tzinfo=UTC)
    await session.flush()

    sql_statements.clear()
    latest = await get_latest_problems(session, limit=2)

    assert [item.arena_number for item in latest] == [702, 701]
    assert len(sql_statements) == 1
    statement = sql_statements[0].lower()
    assert "problem_statement" not in statement
    assert "problem_image_base64" not in statement
    assert "owner_id" not in statement


def test_problem_list_uses_distinct_aggregate_and_personal_solved_labels() -> None:
    """Problem list headers must distinguish aggregate solvers from personal solved status."""
    template = _TEMPLATE.read_text(encoding="utf-8")

    assert "User Solves" in template
    assert "solvers_asc" in template
    assert "solvers_desc" in template
    assert "Solved?" in template
    assert '<th class="column-width-tiny">Solved</th>' not in template


def test_problem_list_template_includes_custom_validator_legend_and_marker() -> None:
    template = _TEMPLATE.read_text(encoding="utf-8")

    assert "Problems marked with" in template
    assert "use a custom validator" in template
    assert "published_with_changes" in template
    assert "item.has_custom_validator" in template


@pytest.mark.asyncio
async def test_collection_scope_ands_with_the_category_or_set(session: AsyncSession) -> None:
    """The exact case from issue #185.

    Categories OR together, so ``interif OR arvores OR grafos`` also returns
    Maratona SBC problems. Scoping to a collection must narrow that set rather
    than widen it, making "InterIF problems about trees or graphs" expressible.
    """
    owner = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    interif = ArenaCollection(name="InterIF", slug="interif")
    sbc = ArenaCollection(name="Maratona SBC", slug="maratona-sbc")
    trees = ArenaCategory(name="Arvores", slug="arvores")
    graphs = ArenaCategory(name="Grafos", slug="grafos")
    strings = ArenaCategory(name="Strings", slug="strings")
    session.add_all([interif, sbc, trees, graphs, strings])
    await session.flush()

    wanted = await _make_problem(session, owner, arena_number=451, title="InterIF trees")
    also_wanted = await _make_problem(session, owner, arena_number=452, title="InterIF graphs")
    wrong_category = await _make_problem(session, owner, arena_number=453, title="InterIF strings")
    wrong_collection = await _make_problem(session, owner, arena_number=454, title="SBC graphs")
    unfiled = await _make_problem(session, owner, arena_number=455, title="Unfiled trees")
    wanted.collection_id = interif.id
    also_wanted.collection_id = interif.id
    wrong_category.collection_id = interif.id
    wrong_collection.collection_id = sbc.id
    await session.flush()
    await session.execute(
        arena_problem_category_map.insert(),
        [
            {"problem_id": wanted.id, "category_id": trees.id},
            {"problem_id": also_wanted.id, "category_id": graphs.id},
            {"problem_id": wrong_category.id, "category_id": strings.id},
            {"problem_id": wrong_collection.id, "category_id": graphs.id},
            {"problem_id": unfiled.id, "category_id": trees.id},
        ],
    )
    await session.flush()

    scoped = await list_enabled_problems_paginated(
        session,
        page=1,
        category_slugs=["arvores", "grafos"],
        collection_id=interif.id,
    )

    assert sorted(item.arena_number for item in scoped.items) == [451, 452]
    assert scoped.total == 2

    # Without the scope the same category filter reaches into every collection.
    unscoped = await list_enabled_problems_paginated(session, page=1, category_slugs=["arvores", "grafos"])
    assert sorted(item.arena_number for item in unscoped.items) == [451, 452, 454, 455]


@pytest.mark.asyncio
async def test_collection_scope_alone_returns_the_whole_collection(session: AsyncSession) -> None:
    """A collection with no category filter returns exactly its own problems."""
    owner = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    interif = ArenaCollection(name="InterIF", slug="interif")
    session.add(interif)
    await session.flush()
    inside = await _make_problem(session, owner, arena_number=461, title="Inside")
    inside.collection_id = interif.id
    await _make_problem(session, owner, arena_number=462, title="Outside")
    await session.flush()

    pagination = await list_enabled_problems_paginated(session, page=1, collection_id=interif.id)

    assert [item.arena_number for item in pagination.items] == [461]


@pytest.mark.asyncio
async def test_collection_cards_count_only_enabled_problems(session: AsyncSession) -> None:
    """A card must not advertise problems the catalogue will not show."""
    owner = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    interif = ArenaCollection(name="InterIF", slug="interif")
    empty = ArenaCollection(name="Empty", slug="empty")
    session.add_all([interif, empty])
    await session.flush()
    visible = await _make_problem(session, owner, arena_number=471, title="Visible")
    hidden = await _make_problem(session, owner, arena_number=472, title="Hidden")
    visible.collection_id = interif.id
    hidden.collection_id = interif.id
    hidden.enabled = False
    await session.flush()

    cards = await list_collections_with_counts(session)

    counts = {card.name: card.problem_count for card in cards}
    assert counts == {"Empty": 0, "InterIF": 1}
    interif_card = next(card for card in cards if card.slug == "interif")
    assert interif_card.name == "InterIF"
