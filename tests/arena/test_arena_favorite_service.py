#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for Arena favorite service operations."""

import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.models.arena_users import ArenaUser
from arena.services.arena_favorite_service import (
    get_favorites_for_problems,
    is_favorite,
    toggle_favorite,
)
from shared.db_schema.arena import arena_problems
from shared.enumerations import ArenaRole


async def _make_user(session: AsyncSession) -> ArenaUser:
    """Create and persist an Arena user."""
    user = ArenaUser(
        nome=f"Favorite User {uuid.uuid4().hex[:6]}",
        email_normalizado=f"favorite-{uuid.uuid4().hex[:8]}@test.example",
        dta_nascimento=date(2000, 1, 1),
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        consentimento_responsavel=True,
    )
    user.password = "Senha@Forte1!"
    session.add(user)
    await session.flush()
    return user


async def _make_problem(session: AsyncSession, *, owner_id: str, title: str) -> str:
    """Create a persisted arena problem and return its id."""
    problem_id = str(uuid.uuid4())
    await session.execute(
        arena_problems.insert().values(
            id=problem_id,
            arena_number=int(uuid.uuid4().int % 1_000_000_000) + 1,
            title=title,
            owner_id=owner_id,
            problem_statement="<p>Favorite test.</p>",
        )
    )
    await session.flush()
    return problem_id


@pytest.mark.asyncio
async def test_toggle_favorite_adds_then_removes(session: AsyncSession) -> None:
    """Toggle should add favorite on first call and remove on second call."""
    user = await _make_user(session)
    problem_id = await _make_problem(session, owner_id=user.id, title="Fav Problem")

    assert await is_favorite(session, user_id=user.id, problem_id=problem_id) is False

    now_favorite = await toggle_favorite(session, user_id=user.id, problem_id=problem_id)
    assert now_favorite is True
    assert await is_favorite(session, user_id=user.id, problem_id=problem_id) is True

    now_favorite = await toggle_favorite(session, user_id=user.id, problem_id=problem_id)
    assert now_favorite is False
    assert await is_favorite(session, user_id=user.id, problem_id=problem_id) is False


@pytest.mark.asyncio
async def test_toggle_favorite_insert_race_is_ignored(session: AsyncSession) -> None:
    """A row inserted between delete and insert should not crash toggle."""
    user = await _make_user(session)
    problem_id = await _make_problem(session, owner_id=user.id, title="Concurrent Fav")

    real_execute = session.execute
    injected = False

    async def _execute_with_race(statement, *args, **kwargs):
        nonlocal injected
        result = await real_execute(statement, *args, **kwargs)
        is_delete_favorites = (
            not injected
            and getattr(statement, "__visit_name__", "") == "delete"
            and getattr(statement, "table", None) is not None
            and statement.table.name == "arena_problem_favorites"
        )
        if is_delete_favorites:
            await real_execute(
                statement.table.insert().values(
                    user_id=user.id,
                    problem_id=problem_id,
                )
            )
            injected = True
        return result

    session.execute = _execute_with_race  # type: ignore[method-assign]

    now_favorite = await toggle_favorite(session, user_id=user.id, problem_id=problem_id)

    assert now_favorite is True
    favorites = await get_favorites_for_problems(session, user_id=user.id, problem_ids=[problem_id])
    assert favorites == {problem_id}


@pytest.mark.asyncio
async def test_get_favorites_for_problems_returns_only_requested_subset(session: AsyncSession) -> None:
    """Bulk favorites lookup should return only favorited ids from requested list."""
    user = await _make_user(session)
    p1 = await _make_problem(session, owner_id=user.id, title="P1")
    p2 = await _make_problem(session, owner_id=user.id, title="P2")
    p3 = await _make_problem(session, owner_id=user.id, title="P3")

    await toggle_favorite(session, user_id=user.id, problem_id=p1)
    await toggle_favorite(session, user_id=user.id, problem_id=p3)

    favorites = await get_favorites_for_problems(session, user_id=user.id, problem_ids=[p1, p2])

    assert favorites == {p1}
