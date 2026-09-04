#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the team solved-problem shelf on the contest dashboard."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import submission_judgments as submission_judgments_table
from shared.enumerations import JudgmentStatus, ProblemValidatorType, Verdict
from web.dependencies import ContestContext
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem
from web.models.submission import Submission, SubmissionJudgment
from web.models.users import User
from web.routes.generaluser_dashboard import _build_team_solved_problems

_BASE_TIME = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


async def _make_language(session: AsyncSession) -> Language:
    """Create the language required by contest submission rows."""
    language = Language(
        id="dashboard-python",
        name="Python",
        icon="devicon-python-plain",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="source.py",
        artifact_path="/sandbox/source.py",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(language)
    await session.flush()
    return language


async def _make_problem(
    session: AsyncSession,
    contest: Contest,
    *,
    ordinal: int,
    title: str,
    color: str,
) -> Problem:
    """Create one contest problem for the solved shelf."""
    problem = Problem(
        contest_id=contest.id,
        title=title,
        ordinal=ordinal,
        color=color,
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(problem)
    await session.flush()
    return problem


async def _make_judged_submission(
    session: AsyncSession,
    *,
    problem: Problem,
    team: User,
    language: Language,
    seconds: int,
    verdict: Verdict,
    status: JudgmentStatus = JudgmentStatus.DONE,
) -> Submission:
    """Create a submission carrying the requested persisted final verdict."""
    source = f"print('{problem.id}-{team.id}-{seconds}')\n"
    created_at = _BASE_TIME + timedelta(seconds=seconds)
    submission = Submission(
        problem_id=problem.id,
        team_id=team.id,
        language_id=language.id,
        source_code=source,
        source_hash=hashlib.sha256(source.encode()).hexdigest(),
        source_size_bytes=len(source.encode()),
        timestamp_seconds=seconds,
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(submission)
    await session.flush()

    judgment = SubmissionJudgment(
        submission_id=submission.id,
        status=status,
        autojudge_verdict=verdict,
        final_verdict=verdict,
        created_at=created_at,
    )
    session.add(judgment)
    await session.flush()
    await session.execute(
        update(submission_judgments_table)
        .where(submission_judgments_table.c.id == judgment.id)
        .values(final_verdict=verdict.value)
    )
    return submission


@pytest.mark.asyncio
async def test_solved_shelf_is_ordered_unique_and_marks_only_the_first_team(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    another_team_user: User,
) -> None:
    """Every solved problem appears once, in ordinal order, with first-solve truth."""
    language = await _make_language(session)
    problem_b = await _make_problem(
        session,
        running_contest,
        ordinal=2,
        title="Bridges",
        color="#00aaee",
    )
    problem_a = await _make_problem(
        session,
        running_contest,
        ordinal=1,
        title="Arrays",
        color="#dd2233",
    )

    await _make_judged_submission(
        session,
        problem=problem_a,
        team=another_team_user,
        language=language,
        seconds=10,
        verdict=Verdict.AC,
    )
    await _make_judged_submission(
        session,
        problem=problem_a,
        team=team_user,
        language=language,
        seconds=20,
        verdict=Verdict.AC,
    )
    await _make_judged_submission(
        session,
        problem=problem_b,
        team=team_user,
        language=language,
        seconds=30,
        verdict=Verdict.AC,
    )
    await _make_judged_submission(
        session,
        problem=problem_b,
        team=team_user,
        language=language,
        seconds=40,
        verdict=Verdict.AC,
    )

    rows = await _build_team_solved_problems(
        ContestContext(contest=running_contest, session=session, actor=team_user),
        team_user.id,
    )

    assert [(row.label, row.title, row.color, row.is_first_solve) for row in rows] == [
        ("A", "Arrays", "dd2233", False),
        ("B", "Bridges", "00aaee", True),
    ]


@pytest.mark.asyncio
async def test_solved_shelf_honors_pe_policy_and_ignores_superseded_judgments(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
) -> None:
    """The shelf shares the contest's accepted-verdict and active-judgment rules."""
    language = await _make_language(session)
    pe_problem = await _make_problem(
        session,
        running_contest,
        ordinal=1,
        title="Presentation",
        color="#22aa44",
    )
    superseded_problem = await _make_problem(
        session,
        running_contest,
        ordinal=2,
        title="Old verdict",
        color="#aa44cc",
    )
    await _make_judged_submission(
        session,
        problem=pe_problem,
        team=team_user,
        language=language,
        seconds=10,
        verdict=Verdict.PE,
    )
    await _make_judged_submission(
        session,
        problem=superseded_problem,
        team=team_user,
        language=language,
        seconds=20,
        verdict=Verdict.AC,
        status=JudgmentStatus.SUPERSEDED,
    )
    ctx = ContestContext(contest=running_contest, session=session, actor=team_user)

    running_contest.accept_pe = False
    assert await _build_team_solved_problems(ctx, team_user.id) == []

    running_contest.accept_pe = True
    rows = await _build_team_solved_problems(ctx, team_user.id)
    assert [row.label for row in rows] == ["A"]
