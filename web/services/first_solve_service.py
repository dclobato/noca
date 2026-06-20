#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Helpers for detecting the first accepted solve per problem."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import JudgmentStatus, Verdict
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.submission import Submission, SubmissionJudgment


def accepted_verdicts(*, accept_pe: bool) -> tuple[Verdict, ...]:
    """Return verdicts that count as accepted for a contest."""
    return (Verdict.AC, Verdict.PE) if accept_pe else (Verdict.AC,)


async def first_accepted_submission_ids_by_problem(
    session: AsyncSession,
    contest: Contest,
) -> dict[str, str]:
    """Return the earliest accepted submission id for each problem in a contest."""
    result = await session.execute(
        select(Submission.problem_id, Submission.id)
        .join(Problem, Submission.problem_id == Problem.id)
        .join(
            SubmissionJudgment,
            SubmissionJudgment.submission_id == Submission.id,
        )
        .where(
            Problem.contest_id == contest.id,
            SubmissionJudgment.status != JudgmentStatus.SUPERSEDED,
            SubmissionJudgment.final_verdict.in_(accepted_verdicts(accept_pe=contest.accept_pe)),
        )
        .order_by(Submission.problem_id, Submission.timestamp_seconds, Submission.created_at, Submission.id)
    )
    first_by_problem: dict[str, str] = {}
    for problem_id, submission_id in result.all():
        first_by_problem.setdefault(str(problem_id), str(submission_id))
    return first_by_problem


async def is_first_accepted_submission(
    session: AsyncSession,
    contest: Contest,
    submission: Submission,
) -> bool:
    """Return whether *submission* is the earliest accepted solve for its problem."""
    first_by_problem = await first_accepted_submission_ids_by_problem(session, contest)
    return first_by_problem.get(str(submission.problem_id)) == str(submission.id)
