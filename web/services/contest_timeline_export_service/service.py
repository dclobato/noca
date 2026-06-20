#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Async orchestration for contest timeline exports."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from web.models.clarification import Clarification
from web.models.contest import Contest, Task
from web.models.problem import Problem
from web.models.submission import Submission, SubmissionJudgment
from web.models.users import User

from .common import attachment_safe, render_markdown, sort_datetime
from .events import build_clarification_events, build_task_events, contest_boundary_events
from .submissions import build_submission_contexts, build_submission_events, confirmation_by_judgment


async def build_contest_timeline_report(
    session: AsyncSession,
    contest: Contest,
) -> tuple[str, str]:
    """Build a markdown contest timeline export."""
    users = (
        (
            await session.execute(
                select(User).where(User.contest_id == contest.id).order_by(User.fullname, User.username)
            )
        )
        .scalars()
        .all()
    )
    users_by_id = {user.id: user for user in users}

    problems = (
        (await session.execute(select(Problem).where(Problem.contest_id == contest.id).order_by(Problem.ordinal)))
        .scalars()
        .all()
    )
    problems_by_id = {problem.id: problem for problem in problems}

    submissions = (
        (
            await session.execute(
                select(Submission)
                .execution_options(populate_existing=True)
                .join(Problem, Submission.problem_id == Problem.id)
                .where(Problem.contest_id == contest.id)
                .options(
                    selectinload(Submission.judgments).selectinload(SubmissionJudgment.audit_logs),
                    selectinload(Submission.judgments).selectinload(SubmissionJudgment.confirmations),
                    selectinload(Submission.judgments).selectinload(SubmissionJudgment.overrides),
                )
                .order_by(Submission.timestamp_seconds, Submission.created_at, Submission.id)
            )
        )
        .scalars()
        .all()
    )
    contexts = build_submission_contexts(submissions, users_by_id, problems_by_id)
    all_confirmations = [
        confirmation
        for submission in submissions
        for judgment in submission.judgments
        for confirmation in judgment.confirmations
    ]
    confirmations_by_judgment = confirmation_by_judgment(all_confirmations)

    clarifications = (
        (
            await session.execute(
                select(Clarification)
                .join(Problem, Clarification.problem_id == Problem.id)
                .where(Problem.contest_id == contest.id)
                .order_by(Clarification.created_timestamp_seconds, Clarification.created_at, Clarification.id)
            )
        )
        .scalars()
        .all()
    )

    tasks = (
        (
            await session.execute(
                select(Task)
                .outerjoin(Problem, Task.problem_id == Problem.id)
                .where(
                    or_(
                        Problem.contest_id == contest.id,
                        Task.team_id.in_(list(users_by_id.keys())),
                    )
                )
                .order_by(Task.created_timestamp_seconds, Task.created_at, Task.id)
            )
        )
        .scalars()
        .all()
    )
    tasks = [task for task in tasks if task.problem_id is None or (task.problem_id in problems_by_id)]

    events = contest_boundary_events(contest)
    events.extend(
        build_submission_events(
            contest=contest,
            submissions=submissions,
            users_by_id=users_by_id,
            contexts=contexts,
            confirmations_by_judgment=confirmations_by_judgment,
        )
    )
    events.extend(
        build_clarification_events(
            contest=contest,
            clarifications=clarifications,
            users_by_id=users_by_id,
            problems_by_id=problems_by_id,
        )
    )
    events.extend(
        build_task_events(
            contest=contest,
            tasks=tasks,
            users_by_id=users_by_id,
            problems_by_id=problems_by_id,
        )
    )
    events.sort(key=lambda item: (item.timestamp_seconds, sort_datetime(item.created_at), item.kind, item.sequence))
    contest_end_seconds = contest.duration_minutes * 60
    events = [event for event in events if event.timestamp_seconds <= contest_end_seconds]

    filename = f"contest-timeline-{attachment_safe(contest.login_slug)}.md"
    return filename, render_markdown(contest, events)
