#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Verdict override flows."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.enumerations import JudgmentStatus, Verdict
from shared.timing import compute_timestamp_seconds
from web.models import Contest, Submission, SubmissionJudgment, User, VerdictOverride
from web.models._base import _utcnow
from web.services.judgment_utils import get_active_judgment

from .errors import JudgmentNotDoneError, SameVerdictError


async def override_verdict(
    session: AsyncSession,
    submission_id: str,
    new_verdict: Verdict,
    reason: str,
    chief_judge: User,
    contest: Contest,
) -> VerdictOverride:
    """Persist a chief-judge verdict override."""
    result = await session.execute(
        select(Submission)
        .where(Submission.id == submission_id)
        .options(
            selectinload(Submission.judgments).selectinload(SubmissionJudgment.overrides),
            selectinload(Submission.problem),
        )
    )
    submission = result.scalar_one_or_none()
    if submission is None or submission.problem.contest_id != contest.id:
        raise HTTPException(status_code=404)
    if chief_judge.id != contest.chief_judge_id:
        raise HTTPException(status_code=403)

    active_judgment = get_active_judgment(submission)
    if active_judgment is None:
        raise HTTPException(status_code=404)
    if active_judgment.status != JudgmentStatus.DONE:
        raise JudgmentNotDoneError

    current_verdict = active_judgment.final_verdict
    if current_verdict is None:
        raise HTTPException(status_code=400, detail="The active judgment does not have a final verdict yet.")
    if new_verdict == current_verdict:
        raise SameVerdictError

    now = _utcnow()
    override = VerdictOverride(
        judgment=active_judgment,
        submission=submission,
        overridden_by=chief_judge.id,
        original_verdict=current_verdict,
        new_verdict=new_verdict,
        reason=reason,
        created_at=now,
        timestamp_seconds=compute_timestamp_seconds(contest.start_time, now),
    )
    session.add(override)
    await session.flush()
    return override
