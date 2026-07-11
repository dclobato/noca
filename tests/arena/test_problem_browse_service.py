#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
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
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_users import ArenaUser
from arena.services.problem_browse_service import list_enabled_problems_paginated
from shared.db_schema.arena import arena_problem_ratings, arena_problem_solvers
from shared.enumerations import ArenaRole

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


async def _make_problem(
    session: AsyncSession,
    owner: ArenaUser,
    *,
    arena_number: int | None = None,
    title: str | None = None,
    author: str | None = None,
) -> ArenaProblem:
    problem = ArenaProblem(
        arena_number=arena_number or int(uuid.uuid4().int % 1_000_000_000) + 1,
        title=title or f"Browse Problem {uuid.uuid4().hex[:8]}",
        owner_id=owner.id,
        author=author,
        author_is_owner=author is None,
        enabled=True,
        problem_statement="<p>Echo.</p>",
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
    assert item.problem.id == problem.id
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
    authors_by_problem = {item.problem.id: item.author_name for item in pagination.items}
    assert authors_by_problem[owner_problem.id] == owner.nome
    assert authors_by_problem[external_problem.id] == "Guest Writer"

    external_search = await list_enabled_problems_paginated(
        session,
        page=1,
        search="Guest Writer",
    )
    assert [item.problem.id for item in external_search.items] == [external_problem.id]


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
    assert [item.problem.arena_number for item in pagination.items] == [102, 101, 103]
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

    assert [item.problem.arena_number for item in pagination.items] == [302, 303, 301]
    assert [item.solved for item in pagination.items] == [None, None, 1]


def test_problem_list_uses_distinct_aggregate_and_personal_solved_labels() -> None:
    """Problem list headers must distinguish aggregate solvers from personal solved status."""
    template = _TEMPLATE.read_text(encoding="utf-8")

    assert "User Solves" in template
    assert "solvers_asc" in template
    assert "solvers_desc" in template
    assert "Solved?" in template
    assert '<th class="column-width-tiny">Solved</th>' not in template
