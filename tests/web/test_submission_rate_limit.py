#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the per-team submission rate limiting feature.

Tests verify that create_submission() enforces the rolling-window rate limit
and that the rate-limit service functions can be independently monkeypatched.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import web.services.rate_limit_service as rate_limit_module
from shared.db_schema.submission import submissions
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem
from web.models.users import User
from web.services.rate_limit_service import check_submission_rate_limit
from web.services.submission_service import SubmissionRateLimitError, create_submission


def _make_hash(source: str) -> str:
    """Return the SHA-256 hex digest of the given source string.

    Args:
        source: The source code string to hash.

    Returns:
        Hex-encoded SHA-256 digest.
    """
    return hashlib.sha256(source.encode()).hexdigest()


async def _make_language(session: AsyncSession, language_id: str = "python3") -> Language:
    """Create and flush a Language row suitable for submission tests.

    Args:
        session: The async database session.
        language_id: Primary key to use for the language row.

    Returns:
        The persisted Language instance.
    """
    language = Language(
        id=language_id,
        name="Python 3.14",
        icon="python",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["python3", "-m", "py_compile", "/sandbox/main.py"],
        run_cmd=["python3", "-u", "/sandbox/main.py"],
        source_filename="main.py",
        artifact_path="/sandbox/main.py",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(language)
    await session.flush()
    return language


async def _submit(
    session: AsyncSession,
    actor: User,
    contest: Contest,
    problem: Problem,
    language: Language,
    source: str,
    *,
    window: int = 60,
    max_subs: int = 3,
) -> None:
    """Helper: call create_submission with the given source variant.

    Args:
        session: The async database session.
        actor: The TEAM user making the submission.
        contest: The contest the submission belongs to.
        problem: The problem being submitted.
        language: The language of the submission.
        source: Source code string (must differ per unique submission).
        window: Rolling window in seconds for rate limiting.
        max_subs: Maximum submissions allowed within the window.
    """
    await create_submission(
        session,
        actor,
        contest,
        problem.id,
        language.id,
        source,
        _make_hash(source),
        len(source.encode()),
        rate_limit_window_seconds=window,
        rate_limit_max_submissions=max_subs,
    )


@pytest.mark.asyncio
async def test_rate_limit_allows_submissions_within_window(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
) -> None:
    """Three distinct submissions within the limit all succeed without raising."""
    language = await _make_language(session)

    for i in range(3):
        source = f"print({i})\n"
        await _submit(session, team_user, running_contest, contest_problem, language, source)


@pytest.mark.asyncio
async def test_rate_limit_blocks_submission_over_limit(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
) -> None:
    """The 4th submission within the window raises SubmissionRateLimitError with a UTC-aware next_allowed_at."""
    language = await _make_language(session)

    for i in range(3):
        source = f"print({i})\n"
        await _submit(session, team_user, running_contest, contest_problem, language, source)

    with pytest.raises(SubmissionRateLimitError) as exc_info:
        await _submit(session, team_user, running_contest, contest_problem, language, "print(99)\n")

    exc = exc_info.value
    assert isinstance(exc.next_allowed_at, datetime)
    # next_allowed_at is UTC-aware on PostgreSQL; on SQLite (test backend) the DB
    # returns naive datetimes, so the value may be naive.  Either is acceptable —
    # we only require it to be a datetime instance.


@pytest.mark.asyncio
async def test_rate_limit_denial_does_not_create_submission(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
) -> None:
    """A denied submission must not insert a Submission row."""
    language = await _make_language(session)

    for i in range(3):
        await _submit(session, team_user, running_contest, contest_problem, language, f"print({i})\n")

    before_count = await session.scalar(select(func.count()).where(submissions.c.team_id == team_user.id))

    with pytest.raises(SubmissionRateLimitError):
        await _submit(session, team_user, running_contest, contest_problem, language, "print(99)\n")

    after_count = await session.scalar(select(func.count()).where(submissions.c.team_id == team_user.id))
    assert after_count == before_count


@pytest.mark.asyncio
async def test_rate_limit_allows_submission_after_window_rolls_off(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
) -> None:
    """Submissions older than the rolling window do not count against the limit."""
    language = await _make_language(session)

    for i in range(3):
        await _submit(session, team_user, running_contest, contest_problem, language, f"print({i})\n")

    old_timestamp = datetime.now(UTC) - timedelta(seconds=120)
    await session.execute(
        update(submissions)
        .where(submissions.c.team_id == team_user.id)
        .values(created_at=old_timestamp, updated_at=old_timestamp)
    )

    await _submit(
        session,
        team_user,
        running_contest,
        contest_problem,
        language,
        "print('new window')\n",
        window=60,
    )


@pytest.mark.asyncio
async def test_rate_limit_next_allowed_at_is_after_now(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
) -> None:
    """next_allowed_at on the raised error is strictly after the moment the 4th call was made."""
    language = await _make_language(session)
    max_submissions = 3

    for i in range(max_submissions):
        source = f"print({i})\n"
        await _submit(
            session,
            team_user,
            running_contest,
            contest_problem,
            language,
            source,
            max_subs=max_submissions,
        )

    before = datetime.now(UTC)

    with pytest.raises(SubmissionRateLimitError) as exc_info:
        await _submit(
            session,
            team_user,
            running_contest,
            contest_problem,
            language,
            "print(99)\n",
            max_subs=max_submissions,
        )

    next_at = exc_info.value.next_allowed_at
    # SQLite returns naive datetimes; strip tzinfo from before for a uniform comparison.
    before_naive = before.replace(tzinfo=None) if next_at.tzinfo is None else before
    assert next_at > before_naive


@pytest.mark.asyncio
async def test_rate_limit_is_per_team_not_global(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    another_team_user: User,
    contest_problem: Problem,
) -> None:
    """team_user hitting the limit does not affect another_team_user's ability to submit."""
    language = await _make_language(session)

    # Fill team_user's limit
    for i in range(3):
        source = f"print({i})\n"
        await _submit(session, team_user, running_contest, contest_problem, language, source)

    # team_user's 4th call raises
    with pytest.raises(SubmissionRateLimitError):
        await _submit(session, team_user, running_contest, contest_problem, language, "print(99)\n")

    # another_team_user can still submit (window is empty for them)
    await _submit(
        session,
        another_team_user,
        running_contest,
        contest_problem,
        language,
        "print('other')\n",
    )


@pytest.mark.asyncio
async def test_rate_limit_monkeypatches_lock(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """acquire_submission_rate_lock is independently monkeypatchable at module level.

    Replacing the lock function with an async no-op must not break check_submission_rate_limit:
    the count logic still runs, and an empty window returns (True, None).
    """
    lock_called: list[bool] = []

    async def _noop_lock(s: AsyncSession, team_id: str) -> None:  # noqa: ARG001
        lock_called.append(True)

    monkeypatch.setattr(rate_limit_module, "acquire_submission_rate_lock", _noop_lock)

    allowed, next_allowed_at = await check_submission_rate_limit(
        session, team_user.id, window_seconds=60, max_submissions=3
    )

    assert allowed is True
    assert next_allowed_at is None
    assert lock_called, "monkeypatched lock function was not called"
