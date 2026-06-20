#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Service tests for Arena admin category CRUD."""

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.models.arena_problems import ArenaCategory, ArenaProblem
from arena.models.arena_users import ArenaUser
from arena.services import admin_category_service
from shared.db_schema.arena import arena_problem_category_map
from shared.enumerations import ArenaRole


async def _author(session: AsyncSession) -> ArenaUser:
    """Create a minimal Arena problem owner."""
    user = ArenaUser(
        nome="Author",
        email_normalizado="author@test.example",
        password_hash="hash",
        role=ArenaRole.ARENA_JUDGE,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(1990, 1, 1),
        consentimento_responsavel=True,
    )
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_create_category_normalizes_slug_and_color(session: AsyncSession) -> None:
    category = await admin_category_service.create_category(
        session,
        name="  Dynamic Programming  ",
        slug="Programação Dinâmica",
        color="#ABCDEF",
    )

    assert category.name == "Dynamic Programming"
    assert category.slug == "programacao-dinamica"
    assert category.color == "#abcdef"


@pytest.mark.asyncio
async def test_create_category_rejects_invalid_and_duplicate_values(session: AsyncSession) -> None:
    await admin_category_service.create_category(session, name="Graphs", slug="graphs", color="#0d6efd")

    cases = [
        {"name": "", "slug": "blank-name", "color": "#0d6efd"},
        {"name": "graphs", "slug": "other", "color": "#0d6efd"},
        {"name": "Other", "slug": "", "color": "#0d6efd"},
        {"name": "Other", "slug": "graphs", "color": "#0d6efd"},
        {"name": "Other", "slug": "valid", "color": "blue"},
    ]
    for data in cases:
        with pytest.raises(ValueError):
            await admin_category_service.create_category(session, **data)


@pytest.mark.asyncio
async def test_list_categories_includes_problem_counts(session: AsyncSession) -> None:
    author = await _author(session)
    category = await admin_category_service.create_category(
        session,
        name="Graphs",
        slug="graphs",
        color="#0d6efd",
    )
    problem = ArenaProblem(
        arena_number=1,
        title="Shortest Path",
        owner_id=author.id,
        problem_statement="Find the shortest path.",
    )
    session.add(problem)
    await session.flush()
    await session.execute(arena_problem_category_map.insert().values(problem_id=problem.id, category_id=category.id))

    pagination = await admin_category_service.list_categories_paginated(session, page=1, per_page=25)

    assert pagination.total == 1
    assert pagination.items[0].category.name == "Graphs"
    assert pagination.items[0].problem_count == 1


@pytest.mark.asyncio
async def test_delete_category_removes_links_and_keeps_problems(session: AsyncSession) -> None:
    author = await _author(session)
    category = await admin_category_service.create_category(
        session,
        name="Math",
        slug="math",
        color="#6c757d",
    )
    problem = ArenaProblem(
        arena_number=1,
        title="Prime",
        owner_id=author.id,
        problem_statement="Check primality.",
    )
    session.add(problem)
    await session.flush()
    await session.execute(arena_problem_category_map.insert().values(problem_id=problem.id, category_id=category.id))

    await admin_category_service.delete_category(session, category)
    await session.flush()

    assert await session.get(ArenaCategory, category.id) is None
    assert await session.get(ArenaProblem, problem.id) is not None
    links = await session.execute(select(arena_problem_category_map))
    assert links.all() == []
