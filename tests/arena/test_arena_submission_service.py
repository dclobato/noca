#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for Arena submission creation and autojudge enqueue service."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from _tc_helpers import make_arena_test_case
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
import arena.services.rate_limit_service as arena_rate_limit_module
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_submissions import ArenaSubmission, ArenaSubmissionJudgment
from arena.models.arena_users import ArenaUser
from arena.services.rate_limit_service import check_submission_rate_limit
from arena.services.submission_service import (
    ArenaSubmissionRateLimitError,
    ArenaSubmissionServiceError,
    create_arena_submission,
)
from shared.db_schema.arena import arena_problem_ratings, arena_problem_tried
from shared.db_schema.arena.arena_submissions import arena_submissions
from shared.enumerations import ArenaRole, JudgmentStatus
from web.models.language import Language


async def _make_language(session: AsyncSession, *, active: bool = True) -> Language:
    language = Language(
        id=f"arena-test-{uuid.uuid4().hex[:8]}",
        name="Arena Test Language",
        icon="test",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="main.txt",
        artifact_path="/sandbox/main.txt",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=active,
    )
    session.add(language)
    await session.flush()
    return language


async def _make_user(session: AsyncSession, *, role: ArenaRole = ArenaRole.ARENA_USER) -> ArenaUser:
    user = ArenaUser(
        nome="Arena Submitter",
        email_normalizado=f"submitter-{uuid.uuid4().hex[:8]}@test.example.com",
        dta_nascimento=date(1998, 1, 1),
        role=role,
    )
    user.password = "Senha@Forte1!"
    session.add(user)
    await session.flush()
    return user


async def _make_problem(session: AsyncSession, author: ArenaUser, *, with_case: bool = True) -> ArenaProblem:
    problem = ArenaProblem(
        arena_number=int(uuid.uuid4().int % 1_000_000_000) + 1,
        title=f"Arena Service Problem {uuid.uuid4().hex[:8]}",
        owner_id=author.id,
        problem_statement="<p>Echo.</p>",
    )
    session.add(problem)
    await session.flush()
    if with_case:
        session.add(make_arena_test_case(problem.id, 1))
        await session.flush()
    return problem


@pytest.mark.asyncio
async def test_create_arena_submission_persists_judgment_stats_and_returns_job(session: AsyncSession) -> None:
    """Creating an Arena submission must persist rows and return a ready-to-enqueue job."""
    language = await _make_language(session)
    author = await _make_user(session)
    user = await _make_user(session)
    problem = await _make_problem(session, author)

    result = await create_arena_submission(
        session=session,
        user_id=user.id,
        problem_id=problem.id,
        language_id=language.id,
        source_code="print(input())\n",
    )

    submission = await session.get(ArenaSubmission, result.submission.id)
    judgment = await session.get(ArenaSubmissionJudgment, result.judgment.id)
    assert submission is not None
    assert judgment is not None
    assert judgment.status == JudgmentStatus.QUEUED.value
    assert submission.source_size_bytes == len(b"print(input())\n")

    rating = (
        await session.execute(
            select(
                arena_problem_ratings.c.attempted_users,
                arena_problem_ratings.c.total_submissions,
            ).where(arena_problem_ratings.c.problem_id == problem.id)
        )
    ).one()
    assert rating == (1, 1)

    tried = await session.scalar(
        select(arena_problem_tried.c.problem_id).where(
            arena_problem_tried.c.problem_id == problem.id,
            arena_problem_tried.c.user_id == user.id,
        )
    )
    assert tried == problem.id

    assert result.job.judgment_id == judgment.id
    assert result.job.submission_id == submission.id
    assert result.job.job_kind == "arena_submission"


async def _attempted_and_total(session: AsyncSession, problem_id: str) -> tuple[int, int]:
    """Return ``(attempted_users, total_submissions)`` for a problem's rating row."""
    return (
        await session.execute(
            select(
                arena_problem_ratings.c.attempted_users,
                arena_problem_ratings.c.total_submissions,
            ).where(arena_problem_ratings.c.problem_id == problem_id)
        )
    ).one()


@pytest.mark.asyncio
async def test_create_arena_submission_counts_judge_attempts(session: AsyncSession) -> None:
    """A judge (non-author) submission now counts toward aggregate rating attempts."""
    language = await _make_language(session)
    author = await _make_user(session)
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    problem = await _make_problem(session, author)

    await create_arena_submission(
        session=session,
        user_id=judge.id,
        problem_id=problem.id,
        language_id=language.id,
        source_code="print(input())\n",
        bypass_rate_limit=True,
    )

    assert await _attempted_and_total(session, problem.id) == (1, 1)

    tried = await session.scalar(
        select(arena_problem_tried.c.problem_id).where(
            arena_problem_tried.c.problem_id == problem.id,
            arena_problem_tried.c.user_id == judge.id,
        )
    )
    assert tried == problem.id


@pytest.mark.asyncio
async def test_create_arena_submission_counts_non_owner_staff_as_attempt(session: AsyncSession) -> None:
    """A staff submission on a problem they do not own counts as a rating attempt."""
    language = await _make_language(session)
    author = await _make_user(session)
    admin = await _make_user(session, role=ArenaRole.ARENA_ADMIN)
    problem = await _make_problem(session, author)

    await create_arena_submission(
        session=session,
        user_id=admin.id,
        problem_id=problem.id,
        language_id=language.id,
        source_code="print(input())\n",
        bypass_rate_limit=True,
    )

    # The admin does not own the problem, so the attempt counts; roles are irrelevant.
    assert await _attempted_and_total(session, problem.id) == (1, 1)


@pytest.mark.asyncio
async def test_create_arena_submission_excludes_author_from_attempts(session: AsyncSession) -> None:
    """The problem owner's own submission does not count as a rating attempt."""
    language = await _make_language(session)
    author = await _make_user(session)
    problem = await _make_problem(session, author)

    await create_arena_submission(
        session=session,
        user_id=author.id,
        problem_id=problem.id,
        language_id=language.id,
        source_code="print(input())\n",
        bypass_rate_limit=True,
    )

    assert await _attempted_and_total(session, problem.id) == (0, 1)


@pytest.mark.asyncio
async def test_create_arena_submission_rejects_problem_without_test_cases(session: AsyncSession) -> None:
    """Arena submissions require at least one persisted test case."""
    language = await _make_language(session)
    user = await _make_user(session)
    problem = await _make_problem(session, user, with_case=False)

    with pytest.raises(ArenaSubmissionServiceError, match="no test cases"):
        await create_arena_submission(
            session=session,
            user_id=user.id,
            problem_id=problem.id,
            language_id=language.id,
            source_code="print(1)\n",
        )


@pytest.mark.asyncio
async def test_create_arena_submission_blocks_over_rate_limit(session: AsyncSession) -> None:
    """A denied Arena submission must raise and leave submission rows unchanged."""
    language = await _make_language(session)
    user = await _make_user(session)
    problem = await _make_problem(session, user)

    for i in range(2):
        await create_arena_submission(
            session=session,
            user_id=user.id,
            problem_id=problem.id,
            language_id=language.id,
            source_code=f"print({i})\n",
            rate_limit_window_minutes=5,
            rate_limit_max_submissions=2,
        )

    before_count = await session.scalar(select(func.count()).where(arena_submissions.c.user_id == user.id))

    with pytest.raises(ArenaSubmissionRateLimitError):
        await create_arena_submission(
            session=session,
            user_id=user.id,
            problem_id=problem.id,
            language_id=language.id,
            source_code="print(99)\n",
            rate_limit_window_minutes=5,
            rate_limit_max_submissions=2,
        )

    after_count = await session.scalar(select(func.count()).where(arena_submissions.c.user_id == user.id))
    assert after_count == before_count


@pytest.mark.asyncio
async def test_create_arena_submission_rate_limit_is_per_user(session: AsyncSession) -> None:
    """One Arena user hitting the limit does not block another Arena user."""
    language = await _make_language(session)
    user = await _make_user(session)
    other_user = await _make_user(session)
    problem = await _make_problem(session, user)

    await create_arena_submission(
        session=session,
        user_id=user.id,
        problem_id=problem.id,
        language_id=language.id,
        source_code="print(1)\n",
        rate_limit_window_minutes=5,
        rate_limit_max_submissions=1,
    )

    with pytest.raises(ArenaSubmissionRateLimitError):
        await create_arena_submission(
            session=session,
            user_id=user.id,
            problem_id=problem.id,
            language_id=language.id,
            source_code="print(2)\n",
            rate_limit_window_minutes=5,
            rate_limit_max_submissions=1,
        )

    result = await create_arena_submission(
        session=session,
        user_id=other_user.id,
        problem_id=problem.id,
        language_id=language.id,
        source_code="print(3)\n",
        rate_limit_window_minutes=5,
        rate_limit_max_submissions=1,
    )
    assert result.submission.user_id == other_user.id


@pytest.mark.asyncio
async def test_create_arena_submission_bypass_rate_limit(session: AsyncSession) -> None:
    """The bypass flag must skip the limiter for admin/judge route callers."""
    language = await _make_language(session)
    user = await _make_user(session)
    problem = await _make_problem(session, user)

    await create_arena_submission(
        session=session,
        user_id=user.id,
        problem_id=problem.id,
        language_id=language.id,
        source_code="print(1)\n",
        rate_limit_window_minutes=5,
        rate_limit_max_submissions=1,
    )

    result = await create_arena_submission(
        session=session,
        user_id=user.id,
        problem_id=problem.id,
        language_id=language.id,
        source_code="print(2)\n",
        bypass_rate_limit=True,
        rate_limit_window_minutes=5,
        rate_limit_max_submissions=1,
    )

    assert result.submission.user_id == user.id
    # Over the limit but bypassed: submission created and flagged for the route.
    assert result.rate_limit_exceeded is True
    assert result.rate_limit_next_allowed_at is not None


@pytest.mark.asyncio
async def test_create_arena_submission_within_limit_not_flagged(session: AsyncSession) -> None:
    """A within-limit submission must not be flagged as rate-limited."""
    language = await _make_language(session)
    user = await _make_user(session)
    problem = await _make_problem(session, user)

    result = await create_arena_submission(
        session=session,
        user_id=user.id,
        problem_id=problem.id,
        language_id=language.id,
        source_code="print(1)\n",
        rate_limit_window_minutes=5,
        rate_limit_max_submissions=2,
    )

    assert result.rate_limit_exceeded is False
    assert result.rate_limit_next_allowed_at is None


@pytest.mark.asyncio
async def test_arena_rate_limit_allows_when_window_is_not_full(session: AsyncSession) -> None:
    """Arena limiter allows a user below the rolling-window cap."""
    language = await _make_language(session)
    user = await _make_user(session)
    problem = await _make_problem(session, user)

    await create_arena_submission(
        session=session,
        user_id=user.id,
        problem_id=problem.id,
        language_id=language.id,
        source_code="print(1)\n",
        bypass_rate_limit=True,
    )

    allowed, next_allowed_at = await check_submission_rate_limit(
        session,
        user.id,
        window_minutes=5,
        max_submissions=2,
    )

    assert allowed is True
    assert next_allowed_at is None


@pytest.mark.asyncio
async def test_arena_rate_limit_returns_retry_time(session: AsyncSession) -> None:
    """Arena limiter denial returns the oldest in-window timestamp plus the window."""
    language = await _make_language(session)
    user = await _make_user(session)
    problem = await _make_problem(session, user)

    await create_arena_submission(
        session=session,
        user_id=user.id,
        problem_id=problem.id,
        language_id=language.id,
        source_code="print(1)\n",
        bypass_rate_limit=True,
    )
    await session.flush()

    created_at = datetime.now(UTC) - timedelta(minutes=2)
    await session.execute(
        arena_submissions.update()
        .where(arena_submissions.c.user_id == user.id)
        .values(created_at=created_at, updated_at=created_at)
    )
    await session.flush()

    allowed, next_allowed_at = await check_submission_rate_limit(
        session,
        user.id,
        window_minutes=5,
        max_submissions=1,
    )

    assert allowed is False
    expected = created_at + timedelta(minutes=5)
    if next_allowed_at is not None and next_allowed_at.tzinfo is None:
        expected = expected.replace(tzinfo=None)
    assert next_allowed_at == expected


@pytest.mark.asyncio
async def test_arena_rate_limit_lock_is_monkeypatchable(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Arena limiter should call the lock helper before evaluating the count."""
    user = await _make_user(session)
    lock_called: list[str] = []

    async def _noop_lock(s: AsyncSession, user_id: str) -> None:  # noqa: ARG001
        lock_called.append(user_id)

    monkeypatch.setattr(arena_rate_limit_module, "acquire_submission_rate_lock", _noop_lock)

    allowed, next_allowed_at = await check_submission_rate_limit(
        session,
        user.id,
        window_minutes=5,
        max_submissions=1,
    )

    assert allowed is True
    assert next_allowed_at is None
    assert lock_called == [user.id]
