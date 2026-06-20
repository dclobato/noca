#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Problem limit-change batch helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.enumerations import JudgmentStatus, Verdict
from web.models.contest import Contest
from web.models.problem import (
    Problem,
    ProblemLimitChangeBatch,
    ProblemLimitChangeBatchLanguage,
    ProblemLimitChangeBatchSubmission,
)
from web.models.submission import Submission
from web.models.users import UberAdmin, User
from web.services.judgment_utils import get_active_judgment

from .models import EffectiveProblemLimits


def affected_limit_change_verdicts(contest: Contest) -> tuple[Verdict, ...]:
    """Return final verdicts that must be rejudged after an effective limit change."""
    verdicts = [Verdict.AC, Verdict.RE, Verdict.TLE, Verdict.MLE, Verdict.OLE]
    if contest.accept_pe:
        verdicts.append(Verdict.PE)
    return tuple(verdicts)


async def create_problem_limit_change_batch(
    session: AsyncSession,
    contest: Contest,
    problem: Problem,
    actor: User | UberAdmin,
    changed_limits: dict[str, tuple[str, EffectiveProblemLimits, EffectiveProblemLimits]],
) -> ProblemLimitChangeBatch | None:
    """Persist a stable batch of affected submissions for a running-contest limit change."""
    if not changed_limits:
        return None

    batch = ProblemLimitChangeBatch(
        contest_id=contest.id,
        problem_id=problem.id,
        triggered_by_user_id=actor.id if isinstance(actor, User) else None,
        triggered_by_uberadmin_id=actor.id if isinstance(actor, UberAdmin) else None,
    )
    session.add(batch)
    await session.flush()

    verdicts = affected_limit_change_verdicts(contest)
    result = await session.execute(
        select(Submission)
        .where(
            Submission.problem_id == problem.id,
            Submission.language_id.in_(tuple(changed_limits.keys())),
        )
        .options(selectinload(Submission.judgments))
        .order_by(Submission.created_at.asc(), Submission.id.asc())
    )
    submissions = list(result.scalars().all())
    affected_count = 0
    batch_created_at = datetime.now(UTC)
    for submission in submissions:
        active_judgment = get_active_judgment(submission)
        if active_judgment is None:
            continue
        if active_judgment.status != JudgmentStatus.DONE or active_judgment.final_verdict not in verdicts:
            continue
        session.add(
            ProblemLimitChangeBatchSubmission(
                batch_id=batch.id,
                submission_id=submission.id,
                language_id=submission.language_id,
                original_judgment_id=active_judgment.id,
                original_final_verdict=active_judgment.final_verdict,
                created_at=batch_created_at + timedelta(microseconds=affected_count),
            )
        )
        affected_count += 1

    if affected_count == 0:
        await session.delete(batch)
        await session.flush()
        return None

    for language_id, (change_kind, before_limits, after_limits) in changed_limits.items():
        session.add(
            ProblemLimitChangeBatchLanguage(
                batch_id=batch.id,
                language_id=language_id,
                change_kind=change_kind,
                before_limits=before_limits.as_dict(),
                after_limits=after_limits.as_dict(),
            )
        )

    await session.flush()
    await session.refresh(batch, attribute_names=["submissions", "languages"])
    return batch


async def get_problem_limit_change_batch(
    session: AsyncSession,
    contest: Contest,
    problem_id: str,
    batch_id: str,
) -> ProblemLimitChangeBatch | None:
    """Load one persisted limit-change batch with the data needed by the admin review page."""
    result = await session.execute(
        select(ProblemLimitChangeBatch)
        .where(
            ProblemLimitChangeBatch.id == batch_id,
            ProblemLimitChangeBatch.contest_id == contest.id,
            ProblemLimitChangeBatch.problem_id == problem_id,
        )
        .options(
            selectinload(ProblemLimitChangeBatch.languages).selectinload(ProblemLimitChangeBatchLanguage.language),
            selectinload(ProblemLimitChangeBatch.submissions)
            .selectinload(ProblemLimitChangeBatchSubmission.submission)
            .selectinload(Submission.team)
            .selectinload(User.site),
            selectinload(ProblemLimitChangeBatch.submissions).selectinload(ProblemLimitChangeBatchSubmission.language),
            selectinload(ProblemLimitChangeBatch.submissions).selectinload(
                ProblemLimitChangeBatchSubmission.original_judgment
            ),
            selectinload(ProblemLimitChangeBatch.submissions).selectinload(
                ProblemLimitChangeBatchSubmission.queued_judgment
            ),
        )
    )
    return result.scalar_one_or_none()
