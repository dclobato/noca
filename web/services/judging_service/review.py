#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Review lock acquisition and human confirmation flows."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.enumerations import JudgmentStatus, RoleEnum, Verdict
from shared.services.lock_service import LockClient, acquire_lock, force_release_lock, get_lock, release_lock
from shared.timing import compute_timestamp_seconds
from web.models import Contest, HumanSubmissionConfirmation, Submission, SubmissionJudgment, UberAdmin, User
from web.models._base import _utcnow
from web.services.judgment_utils import get_active_judgment

from .errors import (
    AlreadyConfirmedError,
    JudgmentNotReadyError,
    ReviewAlreadyLockedError,
    ReviewLockUnavailableError,
    ReviewNotHeldByActorError,
)


async def acquire_submission_review(
    session: AsyncSession,
    judgment: SubmissionJudgment,
    actor: User,
    contest: Contest,
    lock_client: LockClient,
) -> SubmissionJudgment:
    """Acquire a review lock for a human judge."""
    if actor.role != RoleEnum.JUDGE:
        raise HTTPException(status_code=403)
    if contest.autojudge_only:
        raise HTTPException(status_code=404)
    if judgment.status != JudgmentStatus.DONE or judgment.final_verdict is not None:
        raise JudgmentNotReadyError

    already_confirmed = (
        await session.execute(
            select(HumanSubmissionConfirmation.id)
            .where(
                HumanSubmissionConfirmation.judgment_id == judgment.id,
                HumanSubmissionConfirmation.judge_id == actor.id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if already_confirmed is not None:
        raise AlreadyConfirmedError

    timeout_minutes = contest.review_timeout_minutes
    ttl_seconds = contest.remaining_time_seconds if timeout_minutes == 0 else timeout_minutes * 60
    acquired = await acquire_lock(
        lock_client,
        kind="review",
        contest_id=contest.id,
        resource_id=judgment.id,
        holder_id=actor.id,
        holder_role=actor.role.value,
        ttl_seconds=ttl_seconds,
    )
    if acquired is None:
        raise ReviewLockUnavailableError
    if not acquired:
        raise ReviewAlreadyLockedError
    return judgment


async def release_submission_review(
    session: AsyncSession,
    judgment: SubmissionJudgment,
    actor: User | UberAdmin,
    contest: Contest,
    lock_client: LockClient,
    *,
    force: bool = False,
) -> None:
    """Release a review lock."""
    lock = await get_lock(lock_client, kind="review", contest_id=contest.id, resource_id=judgment.id)
    if lock is None:
        if force:
            return
        raise ReviewNotHeldByActorError("You do not hold this review lock.")

    if force:
        await force_release_lock(lock_client, kind="review", contest_id=contest.id, resource_id=judgment.id)
        return
    if getattr(actor, "id", None) != lock.holder_id:
        raise ReviewNotHeldByActorError("You do not hold this review lock.")
    await release_lock(
        lock_client,
        kind="review",
        contest_id=contest.id,
        resource_id=judgment.id,
        holder_id=lock.holder_id,
    )


async def confirm_verdict(
    session: AsyncSession,
    submission_id: str,
    verdict: Verdict,
    judge: User,
    contest: Contest,
    lock_client: LockClient,
) -> HumanSubmissionConfirmation:
    """Create a human confirmation for the active judgment of a submission."""
    result = await session.execute(
        select(Submission)
        .where(Submission.id == submission_id)
        .options(
            selectinload(Submission.judgments).selectinload(SubmissionJudgment.confirmations),
            selectinload(Submission.problem),
        )
    )
    submission = result.scalar_one_or_none()
    if submission is None or submission.problem.contest_id != contest.id:
        raise HTTPException(status_code=404)

    active_judgment = get_active_judgment(submission)
    if active_judgment is None:
        raise HTTPException(status_code=404)
    if active_judgment.status != JudgmentStatus.DONE:
        raise JudgmentNotReadyError
    if any(confirmation.judge_id == judge.id for confirmation in active_judgment.confirmations):
        raise AlreadyConfirmedError

    review_lock = await get_lock(lock_client, kind="review", contest_id=contest.id, resource_id=active_judgment.id)
    if review_lock is not None and review_lock.holder_id != judge.id:
        raise ReviewNotHeldByActorError("You must acquire this review before confirming it.")
    if review_lock is None and getattr(lock_client, "is_available", True):
        raise ReviewNotHeldByActorError("You must acquire this review before confirming it.")

    is_chief = contest.chief_judge_id is not None and judge.id == contest.chief_judge_id
    now = _utcnow()
    confirmation = HumanSubmissionConfirmation(
        judgment=active_judgment,
        judge_id=judge.id,
        confirmed_verdict=verdict,
        is_chief_confirmation=is_chief,
        created_at=now,
        timestamp_seconds=compute_timestamp_seconds(contest.start_time, now),
    )
    session.add(confirmation)
    await session.flush()
    if review_lock is not None:
        await release_lock(
            lock_client,
            kind="review",
            contest_id=contest.id,
            resource_id=active_judgment.id,
            holder_id=judge.id,
        )
    return confirmation
