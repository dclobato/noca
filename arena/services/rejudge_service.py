#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena bulk rejudge: build one fresh judgment per settled submission of a problem.

Split out of ``admin_problem_service`` (which is already at the size ceiling)
because the rules here are about judgments, not problems. The contract that
makes ``POST /admin/problems/{id}/rejudge-all`` safe to repeat lives entirely
in this module: submissions whose current judgment is still in flight are
skipped, and the selection runs under row locks so two overlapping requests
cannot both build a judgment for the same submission.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_submissions import ArenaSubmissionJudgment
from shared.db_schema.arena import arena_submission_judgments as _judgments
from shared.db_schema.arena import arena_submissions as _submissions
from shared.enumerations import TERMINAL_JUDGMENT_STATUSES, JudgmentStatus
from shared.queue_schema import ArenaSubmissionJob

_TERMINAL_STATUS_VALUES: tuple[str, ...] = tuple(status.value for status in TERMINAL_JUDGMENT_STATUSES)


@dataclass(frozen=True, slots=True)
class RejudgeBuildResult:
    """What a bulk rejudge decided for one problem.

    Attributes:
        jobs: One ready-to-enqueue job per submission that received a fresh judgment.
        skipped_in_flight: Submissions left alone because a judgment of theirs is
            still ``QUEUED``, ``DISPATCHED`` or ``JUDGING`` -- superseding it would
            orphan work the autojudge is doing right now, and a repeat click is
            exactly this case for every submission.
    """

    jobs: list[ArenaSubmissionJob] = field(default_factory=list)
    skipped_in_flight: int = 0


async def build_rejudge_jobs(session: AsyncSession, problem_id: str) -> RejudgeBuildResult:
    """Supersede each settled submission's active judgments and queue fresh ones.

    Each eligible submission's non-superseded judgments are marked
    ``SUPERSEDED`` before its new ``QUEUED`` judgment is inserted, exactly as
    :func:`arena.services.admin_submission_service.force_rejudge_arena_submission`
    does for one submission: leaving the old judgment active would give the
    submission two live judgments and fan out every query that outer-joins the
    active one.

    The problem's submission rows are locked ``FOR UPDATE`` before the
    in-flight check, so a concurrent request blocks until this one commits and
    then finds every submission in flight. The caller owns the transaction and
    must commit before enqueueing, so the worker never picks up a job whose
    rows are not yet visible.

    Args:
        session: Active async database session (caller commits).
        problem_id: UUID of the problem whose submissions should be re-judged.

    Returns:
        The jobs to enqueue and how many submissions were skipped as in flight.
    """
    in_flight = exists(
        select(_judgments.c.id).where(
            _judgments.c.submission_id == _submissions.c.id,
            _judgments.c.status.not_in(_TERMINAL_STATUS_VALUES),
        )
    )
    rows = (
        await session.execute(
            select(
                _submissions.c.id,
                _submissions.c.user_id,
                _submissions.c.language_id,
                in_flight.label("in_flight"),
            )
            .where(_submissions.c.problem_id == problem_id)
            .with_for_update(of=_submissions)
        )
    ).all()

    jobs: list[ArenaSubmissionJob] = []
    skipped = 0
    for submission_id, user_id, language_id, is_in_flight in rows:
        if is_in_flight:
            skipped += 1
            continue
        await session.execute(
            update(_judgments)
            .where(
                _judgments.c.submission_id == submission_id,
                _judgments.c.status != JudgmentStatus.SUPERSEDED.value,
            )
            .values(status=JudgmentStatus.SUPERSEDED.value)
        )
        judgment = ArenaSubmissionJudgment(
            id=str(uuid.uuid4()),
            submission_id=submission_id,
            status=JudgmentStatus.QUEUED.value,
        )
        session.add(judgment)
        jobs.append(
            ArenaSubmissionJob(
                judgment_id=judgment.id,
                submission_id=submission_id,
                user_id=user_id,
                problem_id=problem_id,
                language_id=language_id,
                requeue_count=0,
            )
        )
    if jobs:
        await session.flush()
    return RejudgeBuildResult(jobs=jobs, skipped_in_flight=skipped)
