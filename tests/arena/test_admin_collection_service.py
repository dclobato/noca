#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Service tests for Arena admin collection CRUD."""

from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.models.arena_problems import ArenaCollection, ArenaProblem
from arena.models.arena_users import ArenaUser
from arena.services import admin_collection_service
from shared.enumerations import ArenaRole, ProblemValidatorType


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


async def _problem(session: AsyncSession, author: ArenaUser, *, number: int, title: str) -> ArenaProblem:
    """Create a minimal Arena problem owned by ``author``."""
    problem = ArenaProblem(
        arena_number=number,
        title=title,
        owner_id=author.id,
        problem_statement="Statement.",
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(problem)
    await session.flush()
    return problem


@pytest.mark.asyncio
async def test_create_collection_normalizes_slug(session: AsyncSession) -> None:
    collection = await admin_collection_service.create_collection(
        session,
        name="  Maratona SBC  ",
        slug="Maratona de Programação da SBC",
    )

    assert collection.name == "Maratona SBC"
    # Stop words ("de", "da") are dropped by the shared taxonomy slug policy.
    assert collection.slug == "maratona-programacao-sbc"


@pytest.mark.asyncio
async def test_create_collection_rejects_invalid_and_duplicate_values(session: AsyncSession) -> None:
    await admin_collection_service.create_collection(session, name="InterIF", slug="interif")

    cases = [
        {"name": "", "slug": "blank-name"},
        {"name": "interif", "slug": "other"},
        {"name": "Other", "slug": ""},
        {"name": "Other", "slug": "interif"},
    ]
    for data in cases:
        with pytest.raises(ValueError):
            await admin_collection_service.create_collection(session, **data)


@pytest.mark.asyncio
async def test_list_collections_includes_problem_counts(session: AsyncSession) -> None:
    author = await _author(session)
    collection = await admin_collection_service.create_collection(session, name="ICPC", slug="icpc")
    await admin_collection_service.create_collection(session, name="Empty", slug="empty")
    problem = await _problem(session, author, number=1, title="Shortest Path")
    problem.collection_id = collection.id
    await session.flush()

    pagination = await admin_collection_service.list_collections_paginated(session, page=1, per_page=25)

    assert pagination.total == 2
    counts = {item.collection.name: item.problem_count for item in pagination.items}
    assert counts == {"ICPC": 1, "Empty": 0}


@pytest.mark.asyncio
async def test_get_collection_by_slug_normalizes_and_misses_cleanly(session: AsyncSession) -> None:
    await admin_collection_service.create_collection(session, name="InterIF", slug="interif")

    found = await admin_collection_service.get_collection_by_slug(session, "  InterIF  ")
    assert found is not None
    assert found.slug == "interif"

    # A blank slug is "no scope", not a miss; an unknown one is a miss.
    assert await admin_collection_service.get_collection_by_slug(session, "   ") is None
    assert await admin_collection_service.get_collection_by_slug(session, "nope") is None


@pytest.mark.asyncio
async def test_delete_collection_unfiles_problems_and_keeps_them(session: AsyncSession) -> None:
    author = await _author(session)
    collection = await admin_collection_service.create_collection(session, name="Maratona SBC", slug="maratona-sbc")
    problem = await _problem(session, author, number=1, title="Prime")
    problem.collection_id = collection.id
    await session.flush()

    await admin_collection_service.delete_collection(session, collection)
    await session.flush()

    assert await session.get(ArenaCollection, collection.id) is None
    surviving = await session.get(ArenaProblem, problem.id)
    assert surviving is not None
    # SET NULL, not CASCADE: deleting a collection must never delete problems.
    assert surviving.collection_id is None
