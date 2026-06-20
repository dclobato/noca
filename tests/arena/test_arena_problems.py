#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for ArenaProblem, ArenaTestCase, and ArenaRatingProblem ORM models.

Covers:
  - Table server defaults applied on insert.
  - Cascade delete: removing ArenaProblem deletes test cases and rating.
  - ArenaRatingProblem computed properties and zero-division guards.
  - Check constraint violations raise IntegrityError.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_problems  # noqa: F401 — registers ORM mappers
import arena.models.arena_users  # noqa: F401 — registers ORM mappers
from arena.models.arena_problems import ArenaCategory, ArenaProblem, ArenaRatingProblem, ArenaTestCase
from arena.models.arena_users import ArenaUser
from arena.services.problem_service import get_problem_by_arena_number
from shared.enumerations import ArenaRole

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def arena_author(session: AsyncSession) -> ArenaUser:
    """Arena user with ARENA_JUDGE role to author problems."""
    user = ArenaUser(
        nome="Autor Teste",
        email_normalizado=f"autor_{uuid.uuid4().hex[:8]}@test.example.com",
        dta_nascimento=date(1990, 1, 1),
        role=ArenaRole.ARENA_JUDGE,
    )
    user.password = "Senha@Forte1!"
    session.add(user)
    await session.flush()
    return user


@pytest_asyncio.fixture
async def arena_problem(session: AsyncSession, arena_author: ArenaUser) -> ArenaProblem:
    """Minimal ArenaProblem with all required fields."""
    problem = ArenaProblem(
        arena_number=1,
        title="Two Sum",
        owner_id=arena_author.id,
        problem_statement="<p>Given an array, return indices of two numbers that add up to target.</p>",
    )
    session.add(problem)
    await session.flush()
    return problem


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_problem_default_limits(session: AsyncSession, arena_problem: ArenaProblem) -> None:
    """Server defaults are applied when no explicit limit values are provided."""
    await session.refresh(arena_problem)
    assert arena_problem.time_limit_ms == 1000
    assert arena_problem.memory_limit_kb == 262144
    assert arena_problem.pids_limit == 64
    assert arena_problem.output_limit_in_bytes == 65536


@pytest.mark.asyncio
async def test_problem_enabled_default(session: AsyncSession, arena_problem: ArenaProblem) -> None:
    """Arena problems are disabled by default."""
    await session.refresh(arena_problem)
    assert arena_problem.enabled is False


@pytest.mark.asyncio
async def test_problem_arena_number_is_persisted(
    session: AsyncSession,
    arena_problem: ArenaProblem,
) -> None:
    """Arena problem numbers are stable public identifiers."""
    await session.refresh(arena_problem)
    assert arena_problem.arena_number == 1


@pytest.mark.asyncio
async def test_get_problem_by_arena_number(
    session: AsyncSession,
    arena_problem: ArenaProblem,
) -> None:
    """Arena problem lookup works through the public sequential number."""
    assert await get_problem_by_arena_number(session, 0) is None
    assert await get_problem_by_arena_number(session, 999) is None

    found = await get_problem_by_arena_number(session, arena_problem.arena_number)

    assert found is not None
    assert found.id == arena_problem.id


@pytest.mark.asyncio
async def test_problem_custom_limits(session: AsyncSession, arena_author: ArenaUser) -> None:
    """Explicit limit values override server defaults."""
    problem = ArenaProblem(
        arena_number=1,
        title="Hard Problem",
        owner_id=arena_author.id,
        problem_statement="Hard.",
        time_limit_ms=3000,
        memory_limit_kb=524288,
        pids_limit=32,
        output_limit_in_bytes=131072,
    )
    session.add(problem)
    await session.flush()
    await session.refresh(problem)
    assert problem.time_limit_ms == 3000
    assert problem.memory_limit_kb == 524288
    assert problem.pids_limit == 32
    assert problem.output_limit_in_bytes == 131072


@pytest.mark.asyncio
async def test_problem_image_mime_is_persisted(session: AsyncSession, arena_author: ArenaUser) -> None:
    """Problem image MIME type is stored with the base64 image payload."""
    problem = ArenaProblem(
        arena_number=1,
        title="Visual Problem",
        owner_id=arena_author.id,
        problem_statement="See the diagram.",
        problem_image_base64="iVBORw0KGgo=",
        problem_image_mime="image/png",
    )
    session.add(problem)
    await session.flush()
    await session.refresh(problem)

    assert problem.problem_image_base64 == "iVBORw0KGgo="
    assert problem.problem_image_mime == "image/png"


@pytest.mark.asyncio
async def test_test_case_is_sample_default(session: AsyncSession, arena_problem: ArenaProblem) -> None:
    """is_sample defaults to False via server default."""
    tc = ArenaTestCase(
        problem_id=arena_problem.id,
        ordinal=1,
        input_size_bytes=4,
        output_size_bytes=2,
    )
    session.add(tc)
    await session.flush()
    await session.refresh(tc)
    assert tc.is_sample is False


@pytest.mark.asyncio
async def test_rating_server_defaults(session: AsyncSession, arena_problem: ArenaProblem) -> None:
    """Rating counters and rating default to 0/0/0/0/50 via server defaults."""
    rating = ArenaRatingProblem(problem_id=arena_problem.id)
    session.add(rating)
    await session.flush()
    await session.refresh(rating)
    assert rating.attempted_users == 0
    assert rating.solved_users == 0
    assert rating.total_submissions == 0
    assert rating.total_tries_before_solve == 0
    assert rating.rating == 50
    assert rating.dta_rating_update is None


@pytest.mark.asyncio
async def test_category_color_default(session: AsyncSession) -> None:
    """Arena categories use a neutral badge color by default."""
    category = ArenaCategory(name="Dynamic Programming", slug="dynamic-programming")
    session.add(category)
    await session.flush()
    await session.refresh(category)

    assert category.color == "#6c757d"


@pytest.mark.asyncio
async def test_category_custom_color(session: AsyncSession) -> None:
    """Arena category badge color is persisted."""
    category = ArenaCategory(name="Graphs", slug="graphs", color="#0d6efd")
    session.add(category)
    await session.flush()
    await session.refresh(category)

    assert category.color == "#0d6efd"


def test_category_foreground_color_picks_best_contrast() -> None:
    """Category foreground color is black or white based on background contrast."""
    assert ArenaCategory(name="Light", slug="light", color="#00ff00").foreground_color == "#000000"
    assert ArenaCategory(name="Dark", slug="dark", color="#003366").foreground_color == "#ffffff"


# ---------------------------------------------------------------------------
# Cascade delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cascade_delete_removes_test_cases(session: AsyncSession, arena_problem: ArenaProblem) -> None:
    """Deleting a problem deletes its test cases via ORM cascade."""
    tc = ArenaTestCase(problem_id=arena_problem.id, ordinal=1, input_size_bytes=2, output_size_bytes=2)
    session.add(tc)
    await session.flush()
    tc_id = tc.id

    await session.delete(arena_problem)
    await session.flush()

    result = await session.execute(select(ArenaTestCase).where(ArenaTestCase.id == tc_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_cascade_delete_removes_rating(session: AsyncSession, arena_problem: ArenaProblem) -> None:
    """Deleting a problem deletes its rating row via ORM cascade."""
    rating = ArenaRatingProblem(problem_id=arena_problem.id)
    session.add(rating)
    await session.flush()
    problem_id = arena_problem.id

    await session.delete(arena_problem)
    await session.flush()

    result = await session.execute(select(ArenaRatingProblem).where(ArenaRatingProblem.problem_id == problem_id))
    assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# Computed properties
# ---------------------------------------------------------------------------


def _make_rating(**kwargs: int) -> ArenaRatingProblem:
    """Build a transient ArenaRatingProblem for computed property tests."""
    return ArenaRatingProblem(
        problem_id="test-problem-id",
        attempted_users=kwargs.get("attempted_users", 0),
        solved_users=kwargs.get("solved_users", 0),
        total_submissions=kwargs.get("total_submissions", 0),
        total_tries_before_solve=kwargs.get("total_tries_before_solve", 0),
        rating=kwargs.get("rating", 50),
    )


def test_failed_users() -> None:
    """failed_users = attempted - solved."""
    r = _make_rating(attempted_users=10, solved_users=3)
    assert r.failed_users == 7


def test_solve_rate_nonzero() -> None:
    """solve_rate = solved / attempted."""
    r = _make_rating(attempted_users=4, solved_users=1)
    assert r.solve_rate == pytest.approx(0.25)


def test_solve_rate_zero_attempts() -> None:
    """solve_rate is 0.0 when no one has attempted."""
    r = _make_rating(attempted_users=0, solved_users=0)
    assert r.solve_rate == 0.0


def test_avg_tries_nonzero() -> None:
    """avg_tries_before_solve = total_tries / solved_users."""
    r = _make_rating(solved_users=2, total_tries_before_solve=6)
    assert r.avg_tries_before_solve == pytest.approx(3.0)


def test_avg_tries_zero_solved() -> None:
    """avg_tries_before_solve is 0.0 when no one solved yet."""
    r = _make_rating(solved_users=0, total_tries_before_solve=0)
    assert r.avg_tries_before_solve == 0.0


def test_rating_confidence_zero() -> None:
    """rating_confidence is 0 when no one has attempted."""
    r = _make_rating(attempted_users=0)
    assert r.rating_confidence == 0


def test_rating_confidence_approaches_100() -> None:
    """rating_confidence is near 100 for a large sample."""
    r = _make_rating(attempted_users=300)
    assert r.rating_confidence >= 99


def test_rating_confidence_midpoint() -> None:
    """rating_confidence is ~63 at 10 attempts (1 - e^-1 ≈ 0.632)."""
    r = _make_rating(attempted_users=10)
    assert 60 <= r.rating_confidence <= 65


# ---------------------------------------------------------------------------
# Check constraint violations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ordinal_zero_rejected(session: AsyncSession, arena_problem: ArenaProblem) -> None:
    """Test case with ordinal=0 violates the CHECK constraint."""
    tc = ArenaTestCase(problem_id=arena_problem.id, ordinal=0)
    session.add(tc)
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_duplicate_ordinal_rejected(session: AsyncSession, arena_problem: ArenaProblem) -> None:
    """Two test cases with the same ordinal violate the UNIQUE constraint."""
    tc1 = ArenaTestCase(problem_id=arena_problem.id, ordinal=1)
    tc2 = ArenaTestCase(problem_id=arena_problem.id, ordinal=1)
    session.add_all([tc1, tc2])
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_duplicate_arena_number_rejected(
    session: AsyncSession,
    arena_author: ArenaUser,
) -> None:
    """Two problems with the same Arena number violate the UNIQUE constraint."""
    p1 = ArenaProblem(
        arena_number=1,
        title="First",
        owner_id=arena_author.id,
        problem_statement="First.",
    )
    p2 = ArenaProblem(
        arena_number=1,
        title="Second",
        owner_id=arena_author.id,
        problem_statement="Second.",
    )
    session.add_all([p1, p2])
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_non_positive_arena_number_rejected(
    session: AsyncSession,
    arena_author: ArenaUser,
) -> None:
    """Arena numbers must be positive."""
    problem = ArenaProblem(
        arena_number=0,
        title="Zero",
        owner_id=arena_author.id,
        problem_statement="Invalid.",
    )
    session.add(problem)
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_rating_out_of_range_rejected(session: AsyncSession, arena_problem: ArenaProblem) -> None:
    """rating=101 violates the BETWEEN 0 AND 100 check."""
    rating = ArenaRatingProblem(problem_id=arena_problem.id, rating=101)
    session.add(rating)
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_solved_exceeds_attempted_rejected(session: AsyncSession, arena_problem: ArenaProblem) -> None:
    """solved_users > attempted_users violates the check constraint."""
    rating = ArenaRatingProblem(
        problem_id=arena_problem.id,
        attempted_users=5,
        solved_users=6,
        total_tries_before_solve=6,
    )
    session.add(rating)
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_tries_less_than_solved_rejected(session: AsyncSession, arena_problem: ArenaProblem) -> None:
    """total_tries_before_solve < solved_users violates the check constraint."""
    rating = ArenaRatingProblem(
        problem_id=arena_problem.id,
        attempted_users=5,
        solved_users=3,
        total_tries_before_solve=2,
    )
    session.add(rating)
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_negative_attempted_users_rejected(session: AsyncSession, arena_problem: ArenaProblem) -> None:
    """Negative attempted_users violates the >= 0 check constraint."""
    rating = ArenaRatingProblem(
        problem_id=arena_problem.id,
        attempted_users=-1,
        solved_users=0,
        total_tries_before_solve=0,
    )
    session.add(rating)
    with pytest.raises(IntegrityError):
        await session.flush()
