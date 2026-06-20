#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Rejudge and post-accept side effects."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.enumerations import JudgmentStatus, TaskType
from shared.services.lock_service import LockClient, force_release_lock
from shared.timing import compute_timestamp_seconds
from web.models import Contest, ProblemLimitChangeBatch, Submission, SubmissionJudgment, UberAdmin, User
from web.models._base import _utcnow
from web.models.contest import Task
from web.services.contest_service import ensure_contest_admin_or_uberadmin
from web.services.first_solve_service import is_first_accepted_submission
from web.services.judgment_utils import get_active_judgment
from web.services.task_service import create_balloon_task

from .errors import NoFinalVerdictError


async def queue_rejudge_for_loaded_submission(
    session: AsyncSession,
    submission: Submission,
    contest: Contest,
    lock_client: LockClient | None,
) -> tuple[SubmissionJudgment, SubmissionJudgment]:
    """Supersede the active judgment and create a new queued judgment."""
    if submission.problem.contest_id != contest.id:
        raise HTTPException(status_code=404)

    active_judgment = get_active_judgment(submission)
    if active_judgment is None:
        raise HTTPException(status_code=404)
    if active_judgment.final_verdict is None:
        raise NoFinalVerdictError

    if lock_client is not None and getattr(lock_client, "is_available", True):
        await force_release_lock(
            lock_client,
            kind="review",
            contest_id=contest.id,
            resource_id=active_judgment.id,
        )

    active_judgment.status = JudgmentStatus.SUPERSEDED
    now = datetime.now(UTC)
    new_judgment = SubmissionJudgment(
        id=str(uuid4()),
        submission_id=submission.id,
        status=JudgmentStatus.QUEUED,
        timestamp_seconds=compute_timestamp_seconds(contest.start_time, now),
    )
    session.add(new_judgment)
    await session.flush()
    return active_judgment, new_judgment


async def rejudge_submission(
    session: AsyncSession,
    submission_id: str,
    chief_judge: User,
    contest: Contest,
    lock_client: LockClient | None = None,
) -> SubmissionJudgment:
    """Queue a rejudge for one submission."""
    if chief_judge.id != contest.chief_judge_id:
        raise HTTPException(status_code=403)

    result = await session.execute(
        select(Submission)
        .where(Submission.id == submission_id)
        .options(selectinload(Submission.judgments), selectinload(Submission.problem))
    )
    submission = result.scalar_one_or_none()
    if submission is None or submission.problem.contest_id != contest.id:
        raise HTTPException(status_code=404)

    _, new_judgment = await queue_rejudge_for_loaded_submission(session, submission, contest, lock_client)
    return new_judgment


async def queue_limit_change_batch_rejudges(
    session: AsyncSession,
    batch: ProblemLimitChangeBatch,
    contest: Contest,
    actor: User | UberAdmin,
    lock_client: LockClient | None,
    *,
    language_id: str | None = None,
) -> list[SubmissionJudgment]:
    """Queue rejudges for pending rows in a persisted problem-limit-change batch."""
    ensure_contest_admin_or_uberadmin(actor)
    if batch.contest_id != contest.id:
        raise HTTPException(status_code=404)

    pending_rows = [
        row
        for row in batch.submissions
        if row.rejudge_status == "PENDING" and (language_id is None or row.language_id == language_id)
    ]
    if not pending_rows:
        return []

    submission_ids = [row.submission_id for row in pending_rows]
    result = await session.execute(
        select(Submission)
        .where(Submission.id.in_(submission_ids))
        .options(selectinload(Submission.judgments), selectinload(Submission.problem))
    )
    submissions = {submission.id: submission for submission in result.scalars().all()}

    now = _utcnow()
    new_judgments: list[SubmissionJudgment] = []
    for row in pending_rows:
        submission = submissions.get(row.submission_id)
        if submission is None:
            row.rejudge_status = "STALE"
            row.resolved_at = now
            continue

        active_judgment = get_active_judgment(submission)
        if (
            active_judgment is None
            or active_judgment.id != row.original_judgment_id
            or active_judgment.status != JudgmentStatus.DONE
            or active_judgment.final_verdict is None
            or active_judgment.final_verdict != row.original_final_verdict
        ):
            row.rejudge_status = "STALE"
            row.resolved_at = now
            continue

        _, new_judgment = await queue_rejudge_for_loaded_submission(
            session,
            submission,
            contest,
            lock_client,
        )
        row.rejudge_status = "QUEUED"
        row.queued_judgment_id = new_judgment.id
        row.resolved_at = now
        new_judgments.append(new_judgment)

    await session.flush()
    return new_judgments


async def create_balloon_task_if_needed(
    session: AsyncSession,
    submission_id: str,
    contest: Contest,
) -> Task | None:
    """Create a balloon task after an accepted submission if needed."""
    result = await session.execute(select(Submission).where(Submission.id == submission_id))
    submission = result.scalar_one_or_none()
    if submission is None:
        return None
    if contest.is_scoreboard_frozen:
        return None

    existing = await session.execute(
        select(Task).where(
            Task.team_id == submission.team_id,
            Task.problem_id == submission.problem_id,
            Task.type.in_((TaskType.BALLOON, TaskType.FIRST_BALLOON)),
        )
    )
    if existing.scalar_one_or_none() is not None:
        return None

    task_type = (
        TaskType.FIRST_BALLOON if await is_first_accepted_submission(session, contest, submission) else TaskType.BALLOON
    )
    if task_type == TaskType.FIRST_BALLOON:
        try:
            async with session.begin_nested():
                return await create_balloon_task(
                    session,
                    contest=contest,
                    problem_id=submission.problem_id,
                    team_id=submission.team_id,
                    task_type=TaskType.FIRST_BALLOON,
                )
        except IntegrityError:
            task_type = TaskType.BALLOON

    return await create_balloon_task(
        session,
        contest=contest,
        problem_id=submission.problem_id,
        team_id=submission.team_id,
        task_type=task_type,
    )
