#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena submission creation and autojudge enqueue service."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problem_sets import ArenaProblemSet
from arena.models.arena_submissions import ArenaSubmission, ArenaSubmissionJudgment
from arena.services.arena_problem_set_service import _is_accepting, _is_active_member
from arena.services.rate_limit_service import check_submission_rate_limit
from shared.db_schema import languages as _language
from shared.db_schema.arena import arena_problem_ratings as _arena_problem_rating
from shared.db_schema.arena import arena_problem_set_problems as _arena_problem_set_problems
from shared.db_schema.arena import arena_problem_tried as _arena_problem_tried
from shared.db_schema.arena import arena_problems as _arena_problem
from shared.db_schema.arena import arena_test_cases as _arena_test_case
from shared.db_schema.arena import arena_users as _arena_user
from shared.enumerations import JudgmentStatus
from shared.queue_schema import ArenaSubmissionJob
from shared.services.arena_query_helpers import is_excluded_from_problem_rating


class ArenaSubmissionServiceError(ValueError):
    """Raised when an Arena submission cannot be accepted."""


class ArenaSubmissionRateLimitError(ArenaSubmissionServiceError):
    """Raised when the user has exceeded the per-window submission rate limit.

    Attributes:
        next_allowed_at: Earliest moment the window will have a free slot.
    """

    def __init__(self, next_allowed_at: datetime) -> None:
        """Initialise with the next-allowed timestamp."""
        super().__init__("Submission rate limit reached.")
        self.next_allowed_at = next_allowed_at


@dataclass(frozen=True)
class ArenaSubmissionCreationResult:
    """Submission and judgment rows created by the Arena submission service.

    Attributes:
        submission: The created ``ArenaSubmission`` ORM row.
        judgment: The created ``ArenaSubmissionJudgment`` ORM row.
        job: The ready-to-enqueue autojudge job.
        rate_limit_exceeded: True when the user was over the rate limit but the
            submission was created anyway because ``bypass_rate_limit`` was set
            (staff). False for regular within-limit submissions.
        rate_limit_next_allowed_at: When ``rate_limit_exceeded`` is True, the
            earliest moment the window would have had a free slot; otherwise None.
    """

    submission: ArenaSubmission
    judgment: ArenaSubmissionJudgment
    job: ArenaSubmissionJob
    rate_limit_exceeded: bool = False
    rate_limit_next_allowed_at: datetime | None = None


async def create_arena_submission(
    *,
    session: AsyncSession,
    user_id: str,
    problem_id: str,
    language_id: str,
    source_code: str,
    problem_set_id: str | None = None,
    bypass_rate_limit: bool = False,
    rate_limit_window_minutes: int = 5,
    rate_limit_max_submissions: int = 10,
) -> ArenaSubmissionCreationResult:
    """Create an Arena submission and update progress counters.

    The caller owns the database transaction and must commit before enqueueing
    the returned job, so the worker never picks up a job whose rows are not yet
    visible in the database. Aggregate rating attempt counters count
    ``ARENA_USER`` submissions only; staff submissions still create tried rows.

    Args:
        session: Active async database session (caller commits).
        user_id: Arena user UUID.
        problem_id: Arena problem UUID.
        language_id: Language key for compilation/execution.
        source_code: Raw source code string.
        problem_set_id: When set, ties the submission to a problem set (making it
            visible to the set's teacher). The set must currently accept submissions,
            contain the problem, and the user must be an active member of its class.
            None (the default) keeps the submission private.
        bypass_rate_limit: When True, skip the per-user rate limit check (e.g. admins/judges).
        rate_limit_window_minutes: Rolling window length for the rate limit check.
        rate_limit_max_submissions: Maximum submissions allowed in the window.

    Returns:
        ArenaSubmissionCreationResult with the ORM rows and the ready-to-enqueue job.

    Raises:
        ArenaSubmissionRateLimitError: When the user exceeds the submission rate limit.
        ArenaSubmissionServiceError: When validation fails (empty code, unknown problem,
            or an invalid problem set tie).
    """
    allowed, next_allowed_at = await check_submission_rate_limit(
        session, user_id, rate_limit_window_minutes, rate_limit_max_submissions
    )
    rate_limit_exceeded = not allowed
    if not allowed and not bypass_rate_limit:
        raise ArenaSubmissionRateLimitError(next_allowed_at)  # type: ignore[arg-type]

    source_bytes = source_code.encode("utf-8")
    if not source_bytes:
        raise ArenaSubmissionServiceError("Source code cannot be empty.")
    # NUL bytes are valid UTF-8 but cannot be stored in PostgreSQL text columns,
    # and never appear in legitimate source code.
    if "\x00" in source_code:
        raise ArenaSubmissionServiceError("Source code must be a text source file.")

    await _validate_problem_language_and_cases(session, problem_id, language_id)

    now = datetime.now(UTC)
    if problem_set_id is not None:
        await _validate_problem_set_tie(session, user_id, problem_id, problem_set_id, now)
    first_attempt = await _is_first_attempt(session, user_id, problem_id)
    is_rating_participant = await _counts_as_rating_attempt(session, user_id, problem_id)
    await _ensure_rating_row(session, problem_id)

    submission = ArenaSubmission(
        id=str(uuid.uuid4()),
        user_id=user_id,
        problem_id=problem_id,
        language_id=language_id,
        source_code=source_code,
        source_hash=hashlib.sha256(source_bytes).hexdigest(),
        source_size_bytes=len(source_bytes),
        problem_set_id=problem_set_id,
    )
    session.add(submission)

    judgment = ArenaSubmissionJudgment(
        id=str(uuid.uuid4()),
        submission_id=submission.id,
        status=JudgmentStatus.QUEUED.value,
    )
    session.add(judgment)

    await _mark_tried(session, user_id, problem_id, now)
    await _increment_submission_stats(
        session,
        problem_id,
        first_attempt=first_attempt,
        count_rating_attempt=is_rating_participant,
    )
    await session.flush()

    job = ArenaSubmissionJob(
        judgment_id=judgment.id,
        submission_id=submission.id,
        user_id=user_id,
        problem_id=problem_id,
        language_id=language_id,
        requeue_count=0,
    )
    return ArenaSubmissionCreationResult(
        submission=submission,
        judgment=judgment,
        job=job,
        rate_limit_exceeded=rate_limit_exceeded,
        rate_limit_next_allowed_at=next_allowed_at if rate_limit_exceeded else None,
    )


async def _validate_problem_language_and_cases(session: AsyncSession, problem_id: str, language_id: str) -> None:
    problem_exists = await session.scalar(select(_arena_problem.c.id).where(_arena_problem.c.id == problem_id))
    if problem_exists is None:
        raise ArenaSubmissionServiceError("Arena problem not found.")

    language_exists = await session.scalar(
        select(_language.c.id).where(
            _language.c.id == language_id,
            _language.c.active.is_(True),
        )
    )
    if language_exists is None:
        raise ArenaSubmissionServiceError("Language is not available.")

    test_case_count = await session.scalar(
        select(func.count()).select_from(_arena_test_case).where(_arena_test_case.c.problem_id == problem_id)
    )
    if int(test_case_count or 0) == 0:
        raise ArenaSubmissionServiceError("Arena problem has no test cases.")


async def _validate_problem_set_tie(
    session: AsyncSession,
    user_id: str,
    problem_id: str,
    problem_set_id: str,
    now: datetime,
) -> None:
    """Validate that the user may tie this submission to the given problem set.

    The set must currently accept submissions (``starts_on <= now <= deadline``),
    contain the problem, and the user must be an active member of the set's class.

    Raises:
        ArenaSubmissionServiceError: When any of those conditions does not hold.
    """
    problem_set = await session.get(ArenaProblemSet, problem_set_id)
    if problem_set is None:
        raise ArenaSubmissionServiceError("Problem set not found.")
    if not _is_accepting(problem_set, now):
        raise ArenaSubmissionServiceError("This problem set is not accepting submissions.")
    in_set = await session.scalar(
        select(_arena_problem_set_problems.c.problem_id).where(
            _arena_problem_set_problems.c.problem_set_id == problem_set_id,
            _arena_problem_set_problems.c.problem_id == problem_id,
        )
    )
    if in_set is None:
        raise ArenaSubmissionServiceError("This problem is not part of the problem set.")
    if not await _is_active_member(session, class_id=problem_set.class_id, user_id=user_id):
        raise ArenaSubmissionServiceError("You are not registered in this problem set's class.")


async def _is_first_attempt(session: AsyncSession, user_id: str, problem_id: str) -> bool:
    existing = await session.scalar(
        select(_arena_problem_tried.c.problem_id).where(
            _arena_problem_tried.c.user_id == user_id,
            _arena_problem_tried.c.problem_id == problem_id,
        )
    )
    return existing is None


async def _counts_as_rating_attempt(session: AsyncSession, user_id: str, problem_id: str) -> bool:
    """Return whether this user's attempt should count toward the rating row.

    Mirrors the rating recompute rule: ARENA_ADMIN submissions and the problem
    author's own submissions are excluded; ARENA_JUDGE and regular ARENA_USER
    attempts count.

    Args:
        session: Active async database session.
        user_id: The submitting user's id.
        problem_id: The problem being attempted.

    Returns:
        bool: True when the attempt should increment ``attempted_users``.
    """
    role = await session.scalar(select(_arena_user.c.role).where(_arena_user.c.id == user_id))
    owner_id = await session.scalar(select(_arena_problem.c.owner_id).where(_arena_problem.c.id == problem_id))
    return not is_excluded_from_problem_rating(role, user_id, owner_id)


async def _ensure_rating_row(session: AsyncSession, problem_id: str) -> None:
    existing = await session.scalar(
        select(_arena_problem_rating.c.problem_id).where(_arena_problem_rating.c.problem_id == problem_id)
    )
    if existing is not None:
        return
    await session.execute(_arena_problem_rating.insert().values(problem_id=problem_id))


async def _mark_tried(session: AsyncSession, user_id: str, problem_id: str, tried_at: datetime) -> None:
    from sqlalchemy.engine import CursorResult

    result: CursorResult[Any] = await session.execute(  # type: ignore[assignment]
        update(_arena_problem_tried)
        .where(
            _arena_problem_tried.c.user_id == user_id,
            _arena_problem_tried.c.problem_id == problem_id,
        )
        .values(last_tried_at=tried_at)
    )
    if result.rowcount:
        return
    await session.execute(
        _arena_problem_tried.insert().values(
            user_id=user_id,
            problem_id=problem_id,
            last_tried_at=tried_at,
        )
    )


async def _increment_submission_stats(
    session: AsyncSession,
    problem_id: str,
    *,
    first_attempt: bool,
    count_rating_attempt: bool,
) -> None:
    values: dict[str, Any] = {
        "total_submissions": _arena_problem_rating.c.total_submissions + 1,
    }
    if first_attempt and count_rating_attempt:
        values["attempted_users"] = _arena_problem_rating.c.attempted_users + 1
    await session.execute(
        update(_arena_problem_rating).where(_arena_problem_rating.c.problem_id == problem_id).values(**values)
    )
