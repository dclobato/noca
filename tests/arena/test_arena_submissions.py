#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for ArenaSubmission, ArenaSubmissionJudgment, ArenaSubmissionTestResult,
ArenaUserSolvedProblem, and ArenaUserTriedProblem ORM models.

Covers:
  - Row insertion and column defaults.
  - Cascade delete: removing a submission deletes its judgments and test results.
  - Cascade delete: removing a user deletes solver and tried rows.
  - UNIQUE constraint on arena_submission_test_results.judgment_id (one result per judgment).
  - FK RESTRICT prevents deletion of a problem with existing submissions.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from _tc_helpers import make_arena_test_case
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_problems  # noqa: F401 — registers ORM mappers
import arena.models.arena_submissions  # noqa: F401 — registers ORM mappers
import arena.models.arena_users  # noqa: F401 — registers ORM mappers
from arena.models.arena_problems import ArenaProblem, ArenaTestCase
from arena.models.arena_submissions import (
    ArenaSubmission,
    ArenaSubmissionJudgment,
    ArenaSubmissionTestResult,
    ArenaUserSolvedProblem,
    ArenaUserTriedProblem,
)
from arena.models.arena_users import ArenaUser
from shared.enumerations import ArenaRole
from web.models.language import Language

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _make_language(session: AsyncSession) -> Language:
    """Create and flush a minimal Language row for FK satisfaction."""
    lang = Language(
        id=f"test-lang-{uuid.uuid4().hex[:6]}",
        name="Test Language",
        icon="test",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="sol.txt",
        artifact_path="/sandbox/sol.txt",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(lang)
    await session.flush()
    return lang


@pytest_asyncio.fixture
async def arena_user(session: AsyncSession) -> ArenaUser:
    """Arena user for submission tests."""
    user = ArenaUser(
        nome="Teste Submissao",
        email_normalizado=f"sub_{uuid.uuid4().hex[:8]}@test.example.com",
        dta_nascimento=date(1995, 6, 15),
        role=ArenaRole.ARENA_USER,
    )
    user.password = "Senha@Forte1!"
    session.add(user)
    await session.flush()
    return user


@pytest_asyncio.fixture
async def arena_author(session: AsyncSession) -> ArenaUser:
    """Separate arena user to author problems."""
    user = ArenaUser(
        nome="Autor Problema",
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
    """Minimal ArenaProblem for submission tests."""
    problem = ArenaProblem(
        arena_number=1,
        title="Submission Test Problem",
        owner_id=arena_author.id,
        problem_statement="<p>Submit something.</p>",
    )
    session.add(problem)
    await session.flush()
    return problem


@pytest_asyncio.fixture
async def arena_test_case(session: AsyncSession, arena_problem: ArenaProblem) -> ArenaTestCase:
    """A single test case for the arena problem."""
    tc = make_arena_test_case(arena_problem.id, 1)
    session.add(tc)
    await session.flush()
    return tc


@pytest_asyncio.fixture
async def arena_submission(
    session: AsyncSession,
    arena_user: ArenaUser,
    arena_problem: ArenaProblem,
) -> ArenaSubmission:
    """A minimal ArenaSubmission."""
    lang = await _make_language(session)
    sub = ArenaSubmission(
        user_id=arena_user.id,
        problem_id=arena_problem.id,
        language_id=lang.id,
        source_code="print('hello')",
        source_hash="a" * 64,
        source_size_bytes=15,
    )
    session.add(sub)
    await session.flush()
    return sub


@pytest_asyncio.fixture
async def arena_judgment(
    session: AsyncSession,
    arena_submission: ArenaSubmission,
) -> ArenaSubmissionJudgment:
    """A QUEUED judgment for the arena submission."""
    judgment = ArenaSubmissionJudgment(
        submission_id=arena_submission.id,
        status="QUEUED",
    )
    session.add(judgment)
    await session.flush()
    return judgment


# ---------------------------------------------------------------------------
# Column defaults and insertion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submission_insert(session: AsyncSession, arena_submission: ArenaSubmission) -> None:
    """ArenaSubmission row is inserted and fields are readable."""
    await session.refresh(arena_submission)
    assert arena_submission.id is not None
    assert arena_submission.source_code == "print('hello')"
    assert arena_submission.source_size_bytes == 15
    assert arena_submission.created_at is not None
    assert arena_submission.updated_at is not None


@pytest.mark.asyncio
async def test_judgment_defaults(
    session: AsyncSession,
    arena_judgment: ArenaSubmissionJudgment,
) -> None:
    """A new judgment has null verdict fields and null timestamps by default."""
    await session.refresh(arena_judgment)
    assert arena_judgment.status == "QUEUED"
    assert arena_judgment.autojudge_verdict is None
    assert arena_judgment.final_verdict is None
    assert arena_judgment.compile_log is None
    assert arena_judgment.started_at is None
    assert arena_judgment.finished_at is None
    assert arena_judgment.created_at is not None


@pytest.mark.asyncio
async def test_judgment_done_fields(
    session: AsyncSession,
    arena_judgment: ArenaSubmissionJudgment,
) -> None:
    """Verdict and timing fields can be set after creation."""
    now = datetime.now(UTC)
    arena_judgment.status = "DONE"
    arena_judgment.autojudge_verdict = "AC"
    arena_judgment.final_verdict = "AC"
    arena_judgment.max_wall_time_ms = 123
    arena_judgment.max_memory_kb = 4096
    arena_judgment.started_at = now
    arena_judgment.finished_at = now
    await session.flush()
    await session.refresh(arena_judgment)
    assert arena_judgment.autojudge_verdict == "AC"
    assert arena_judgment.final_verdict == "AC"
    assert arena_judgment.max_wall_time_ms == 123


# ---------------------------------------------------------------------------
# Cascade delete: submission → judgments → test results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cascade_delete_judgment_on_submission(
    session: AsyncSession,
    arena_submission: ArenaSubmission,
    arena_judgment: ArenaSubmissionJudgment,
) -> None:
    """Deleting a submission cascade-deletes its judgments."""
    judgment_id = arena_judgment.id
    await session.delete(arena_submission)
    await session.flush()
    result = await session.execute(select(ArenaSubmissionJudgment).where(ArenaSubmissionJudgment.id == judgment_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_cascade_delete_test_result_on_judgment(
    session: AsyncSession,
    arena_judgment: ArenaSubmissionJudgment,
    arena_test_case: ArenaTestCase,
) -> None:
    """Deleting a judgment cascade-deletes its test result."""
    result_row = ArenaSubmissionTestResult(
        judgment_id=arena_judgment.id,
        test_case_id=arena_test_case.id,
        verdict="WA",
    )
    session.add(result_row)
    await session.flush()
    result_id = result_row.id

    await session.delete(arena_judgment)
    await session.flush()

    found = await session.execute(select(ArenaSubmissionTestResult).where(ArenaSubmissionTestResult.id == result_id))
    assert found.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# UNIQUE constraint on test results (one per judgment)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_test_result_rejected(
    session: AsyncSession,
    arena_judgment: ArenaSubmissionJudgment,
    arena_test_case: ArenaTestCase,
) -> None:
    """Two test result rows for the same judgment violate the UNIQUE constraint."""
    r1 = ArenaSubmissionTestResult(
        judgment_id=arena_judgment.id,
        test_case_id=arena_test_case.id,
        verdict="WA",
    )
    session.add(r1)
    await session.flush()

    r2 = ArenaSubmissionTestResult(
        judgment_id=arena_judgment.id,
        test_case_id=arena_test_case.id,
        verdict="TLE",
    )
    session.add(r2)
    with pytest.raises(IntegrityError):
        await session.flush()


# ---------------------------------------------------------------------------
# ArenaUserSolvedProblem
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_solver_insert(
    session: AsyncSession,
    arena_user: ArenaUser,
    arena_problem: ArenaProblem,
) -> None:
    """ArenaUserSolvedProblem row is inserted with solved_at."""
    now = datetime.now(UTC)
    solver = ArenaUserSolvedProblem(
        problem_id=arena_problem.id,
        user_id=arena_user.id,
        solved_at=now,
    )
    session.add(solver)
    await session.flush()

    result = await session.execute(
        select(ArenaUserSolvedProblem).where(
            ArenaUserSolvedProblem.user_id == arena_user.id,
            ArenaUserSolvedProblem.problem_id == arena_problem.id,
        )
    )
    row = result.scalar_one()
    assert row.solved_at is not None


@pytest.mark.asyncio
async def test_solver_duplicate_rejected(
    session: AsyncSession,
    arena_user: ArenaUser,
    arena_problem: ArenaProblem,
) -> None:
    """Duplicate (problem_id, user_id) in arena_problem_solvers violates PK."""
    now = datetime.now(UTC)
    s1 = ArenaUserSolvedProblem(problem_id=arena_problem.id, user_id=arena_user.id, solved_at=now)
    s2 = ArenaUserSolvedProblem(problem_id=arena_problem.id, user_id=arena_user.id, solved_at=now)
    session.add_all([s1, s2])
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_cascade_delete_solver_on_user(
    session: AsyncSession,
    arena_user: ArenaUser,
    arena_problem: ArenaProblem,
) -> None:
    """Deleting a user cascade-deletes their solver rows."""
    solver = ArenaUserSolvedProblem(
        problem_id=arena_problem.id,
        user_id=arena_user.id,
        solved_at=datetime.now(UTC),
    )
    session.add(solver)
    await session.flush()

    await session.delete(arena_user)
    await session.flush()

    result = await session.execute(
        select(ArenaUserSolvedProblem).where(ArenaUserSolvedProblem.user_id == arena_user.id)
    )
    assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# ArenaUserTriedProblem
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tried_insert(
    session: AsyncSession,
    arena_user: ArenaUser,
    arena_problem: ArenaProblem,
) -> None:
    """ArenaUserTriedProblem row is inserted with last_tried_at."""
    now = datetime.now(UTC)
    tried = ArenaUserTriedProblem(
        problem_id=arena_problem.id,
        user_id=arena_user.id,
        last_tried_at=now,
    )
    session.add(tried)
    await session.flush()

    result = await session.execute(
        select(ArenaUserTriedProblem).where(
            ArenaUserTriedProblem.user_id == arena_user.id,
            ArenaUserTriedProblem.problem_id == arena_problem.id,
        )
    )
    row = result.scalar_one()
    assert row.last_tried_at is not None


@pytest.mark.asyncio
async def test_tried_duplicate_rejected(
    session: AsyncSession,
    arena_user: ArenaUser,
    arena_problem: ArenaProblem,
) -> None:
    """Duplicate (problem_id, user_id) in arena_problem_tried violates PK."""
    now = datetime.now(UTC)
    t1 = ArenaUserTriedProblem(problem_id=arena_problem.id, user_id=arena_user.id, last_tried_at=now)
    t2 = ArenaUserTriedProblem(problem_id=arena_problem.id, user_id=arena_user.id, last_tried_at=now)
    session.add_all([t1, t2])
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.asyncio
async def test_cascade_delete_tried_on_user(
    session: AsyncSession,
    arena_user: ArenaUser,
    arena_problem: ArenaProblem,
) -> None:
    """Deleting a user cascade-deletes their tried rows."""
    tried = ArenaUserTriedProblem(
        problem_id=arena_problem.id,
        user_id=arena_user.id,
        last_tried_at=datetime.now(UTC),
    )
    session.add(tried)
    await session.flush()

    await session.delete(arena_user)
    await session.flush()

    result = await session.execute(select(ArenaUserTriedProblem).where(ArenaUserTriedProblem.user_id == arena_user.id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_cascade_delete_tried_on_problem(
    session: AsyncSession,
    arena_user: ArenaUser,
    arena_problem: ArenaProblem,
) -> None:
    """Deleting a problem cascade-deletes tried rows (ON CASCADE)."""
    tried = ArenaUserTriedProblem(
        problem_id=arena_problem.id,
        user_id=arena_user.id,
        last_tried_at=datetime.now(UTC),
    )
    session.add(tried)
    await session.flush()

    await session.delete(arena_problem)
    await session.flush()

    result = await session.execute(
        select(ArenaUserTriedProblem).where(ArenaUserTriedProblem.problem_id == arena_problem.id)
    )
    assert result.scalar_one_or_none() is None
