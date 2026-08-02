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
from arena.models.arena_problems import ArenaProblem, ArenaProblemCustomValidator
from arena.models.arena_users import ArenaUser
from arena.services.problem_browse_service import get_latest_problems, list_enabled_problems_paginated
from shared.db_schema.arena import arena_problem_ratings, arena_problem_solvers
from shared.enumerations import ArenaRole, CustomValidatorActiveState, CustomValidatorCandidateState
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
    assert item.rating == 5.0
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
    assert all(item.rating is None and item.ac_rate is None for item in pagination.items)

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
    validator_problem = await _make_problem(session, owner, arena_number=502, title="Interactive")
    candidate_problem = await _make_problem(session, owner, arena_number=503, title="Candidate")
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

    assert len(sql_statements) == 3
    count_sql, page_sql, category_sql = [statement.lower() for statement in sql_statements]
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
    assert [item.id for item in guest_page.items] == [problem.id]
    assert len({item.id for item in guest_page.items}) == len(guest_page.items)

    sql_statements.clear()
    await list_enabled_problems_paginated(session, page=1, user_id=owner.id)
    assert len(sql_statements) == 5


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
