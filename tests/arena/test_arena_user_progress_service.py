#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for Arena user progress profile queries."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.models.arena_users import ArenaUser
from arena.services.pagination_service import PaginationParams
from arena.services.user_progress_service import get_user_progress
from shared.db_schema.arena import (
    arena_problem_categories,
    arena_problem_category_map,
    arena_problem_ratings,
    arena_problem_solvers,
    arena_problem_tried,
    arena_problems,
)
from shared.enumerations import ArenaRole


async def _make_user(session: AsyncSession) -> ArenaUser:
    """Create a persisted Arena user for progress tests."""
    user = ArenaUser(
        nome=f"Progress User {uuid.uuid4().hex[:6]}",
        email_normalizado=f"progress-{uuid.uuid4().hex[:8]}@test.example",
        dta_nascimento=date(2000, 1, 1),
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        consentimento_responsavel=True,
        user_rating=77,
        solved_problems=2,
    )
    user.password = "Senha@Forte1!"
    session.add(user)
    await session.flush()
    return user


async def _make_problem(
    session: AsyncSession,
    author: ArenaUser,
    *,
    title: str,
    rating: int,
    arena_number: int | None = None,
) -> str:
    """Create a problem and rating row."""
    problem_id = str(uuid.uuid4())
    await session.execute(
        arena_problems.insert().values(
            id=problem_id,
            arena_number=arena_number or int(uuid.uuid4().int % 1_000_000_000) + 1,
            title=title,
            owner_id=author.id,
            problem_statement="<p>Progress.</p>",
        )
    )
    await session.execute(
        arena_problem_ratings.insert().values(
            problem_id=problem_id,
            attempted_users=1,
            solved_users=0,
            total_submissions=1,
            total_tries_before_solve=0,
            rating=rating,
        )
    )
    await session.flush()
    return problem_id


async def _make_category(session: AsyncSession, *, name: str) -> str:
    """Create a category and return its id."""
    category_id = str(uuid.uuid4())
    await session.execute(
        arena_problem_categories.insert().values(
            id=category_id,
            name=name,
            slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
            color="#6c757d",
        )
    )
    await session.flush()
    return category_id


async def _tag_problem(session: AsyncSession, *, problem_id: str, category_id: str) -> None:
    """Link a problem to a category."""
    await session.execute(
        arena_problem_category_map.insert().values(
            problem_id=problem_id,
            category_id=category_id,
        )
    )


async def _solve(
    session: AsyncSession,
    *,
    user: ArenaUser,
    problem_id: str,
    solved_at: datetime,
) -> None:
    """Insert a solved progress row."""
    await session.execute(
        arena_problem_solvers.insert().values(
            user_id=user.id,
            problem_id=problem_id,
            solved_at=solved_at,
        )
    )


async def _try_problem(
    session: AsyncSession,
    *,
    user: ArenaUser,
    problem_id: str,
    last_tried_at: datetime,
) -> None:
    """Insert an attempted progress row."""
    await session.execute(
        arena_problem_tried.insert().values(
            user_id=user.id,
            problem_id=problem_id,
            last_tried_at=last_tried_at,
        )
    )


@pytest.mark.asyncio
async def test_progress_solved_rows_are_newest_first_with_fields(session: AsyncSession) -> None:
    """Solved rows should include title, rating, date, and newest-first order."""
    user = await _make_user(session)
    now = datetime.now(UTC)
    older = await _make_problem(session, user, title="Older Solved", rating=40)
    newer = await _make_problem(session, user, title="Newer Solved", rating=80, arena_number=4321)
    category = await _make_category(session, name="Dynamic programming")
    await _tag_problem(session, problem_id=newer, category_id=category)
    await _solve(session, user=user, problem_id=older, solved_at=now - timedelta(days=1))
    await _solve(session, user=user, problem_id=newer, solved_at=now)

    progress = await get_user_progress(
        session=session,
        user=user,
        solved_params=PaginationParams(page=1, per_page=50),
        attempted_params=PaginationParams(page=1, per_page=50),
    )

    assert progress.summary.solved_total == 2
    assert progress.summary.user_rating == 77
    assert [row.title for row in progress.solved.items] == ["Newer Solved", "Older Solved"]
    assert progress.solved.items[0].arena_number == 4321
    assert [category.name for category in progress.solved.items[0].categories] == [
        "Dynamic programming",
    ]
    assert progress.solved.items[0].rating == 8.0
    assert progress.solved.items[0].activity_at == now.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_progress_attempted_rows_are_newest_first_and_exclude_solved(
    session: AsyncSession,
) -> None:
    """Attempted rows should be newest-first and exclude already solved problems."""
    user = await _make_user(session)
    now = datetime.now(UTC)
    solved = await _make_problem(session, user, title="Solved Also Tried", rating=60)
    older = await _make_problem(session, user, title="Older Attempt", rating=30)
    newer = await _make_problem(session, user, title="Newer Attempt", rating=50)
    await _try_problem(session, user=user, problem_id=solved, last_tried_at=now)
    await _solve(session, user=user, problem_id=solved, solved_at=now + timedelta(minutes=1))
    await _try_problem(session, user=user, problem_id=older, last_tried_at=now - timedelta(days=1))
    await _try_problem(session, user=user, problem_id=newer, last_tried_at=now - timedelta(hours=1))

    progress = await get_user_progress(
        session=session,
        user=user,
        solved_params=PaginationParams(page=1, per_page=50),
        attempted_params=PaginationParams(page=1, per_page=50),
    )

    assert [row.title for row in progress.attempted.items] == ["Newer Attempt", "Older Attempt"]
    assert progress.attempted.items[0].rating == 5.0
    assert progress.attempted.total == 2


@pytest.mark.asyncio
async def test_progress_lists_respect_fifty_item_pages_and_totals(session: AsyncSession) -> None:
    """Both progress lists should expose 50-item pagination and total counts."""
    user = await _make_user(session)
    now = datetime.now(UTC)
    for index in range(51):
        solved_problem = await _make_problem(session, user, title=f"Solved {index:02d}", rating=50)
        attempted_problem = await _make_problem(session, user, title=f"Attempted {index:02d}", rating=40)
        await _solve(
            session,
            user=user,
            problem_id=solved_problem,
            solved_at=now - timedelta(minutes=index),
        )
        await _try_problem(
            session,
            user=user,
            problem_id=attempted_problem,
            last_tried_at=now - timedelta(minutes=index),
        )

    progress = await get_user_progress(
        session=session,
        user=user,
        solved_params=PaginationParams(page=1, per_page=50),
        attempted_params=PaginationParams(page=2, per_page=50),
    )

    assert progress.solved.total == 51
    assert len(progress.solved.items) == 50
    assert progress.solved.pages == 2
    assert progress.attempted.total == 51
    assert len(progress.attempted.items) == 1
    assert progress.attempted.page == 2
    assert progress.attempted.items[0].title == "Attempted 50"
