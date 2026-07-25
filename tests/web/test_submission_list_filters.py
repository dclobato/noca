#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for server-side filtering and ordering of contest submissions."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import submission_judgments as submission_judgments_table
from shared.enumerations import JudgmentStatus, Verdict
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem
from web.models.submission import Submission, SubmissionJudgment
from web.models.users import UberAdmin, User
from web.services.submission_service import SubmissionFilters, list_submission_teams, list_submissions

_BASE_TIME = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


async def _make_language(session: AsyncSession) -> Language:
    """Create a language for submission fixtures."""
    language = Language(
        id=f"filter-{uuid4().hex[:8]}",
        name="Filter Test Language",
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


async def _make_submission(
    session: AsyncSession,
    *,
    problem: Problem,
    team: User,
    language: Language,
    created_at: datetime,
    autojudge_verdict: Verdict,
    final_verdict: Verdict,
) -> tuple[Submission, SubmissionJudgment]:
    """Create one submission and persist its judgment verdicts."""
    source_code = f"print('{uuid4().hex}')\n"
    submission = Submission(
        problem_id=problem.id,
        team_id=team.id,
        language_id=language.id,
        source_code=source_code,
        source_hash=hashlib.sha256(source_code.encode()).hexdigest(),
        source_size_bytes=len(source_code.encode()),
        timestamp_seconds=int((created_at - _BASE_TIME).total_seconds()),
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(submission)
    await session.flush()

    judgment = SubmissionJudgment(
        submission_id=submission.id,
        status=JudgmentStatus.DONE,
        autojudge_verdict=autojudge_verdict,
        final_verdict=final_verdict,
        created_at=created_at,
    )
    session.add(judgment)
    await session.flush()
    await session.execute(
        update(submission_judgments_table)
        .where(submission_judgments_table.c.id == judgment.id)
        .values(final_verdict=final_verdict.value)
    )
    return submission, judgment


@pytest.mark.asyncio
async def test_list_submissions_filters_problem_and_team_and_orders_in_sql(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
    team_user: User,
    another_team_user: User,
    contest_problem: Problem,
) -> None:
    """Problem and team filters narrow the ordered database result."""
    language = await _make_language(session)
    second_problem = Problem(
        contest_id=running_contest.id,
        title="Test Problem B",
        ordinal=2,
        color="#00ff00",
    )
    session.add(second_problem)
    await session.flush()

    first, _ = await _make_submission(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        created_at=_BASE_TIME + timedelta(minutes=1),
        autojudge_verdict=Verdict.AC,
        final_verdict=Verdict.AC,
    )
    second, _ = await _make_submission(
        session,
        problem=second_problem,
        team=another_team_user,
        language=language,
        created_at=_BASE_TIME + timedelta(minutes=2),
        autojudge_verdict=Verdict.WA,
        final_verdict=Verdict.WA,
    )

    filtered = await list_submissions(
        session,
        running_contest,
        uberadmin,
        "time_asc",
        filters=SubmissionFilters(
            problem_id=second_problem.id,
            team_id=another_team_user.id,
        ),
    )
    all_by_problem = await list_submissions(session, running_contest, uberadmin, "problem_desc")

    assert [submission.id for submission in filtered] == [second.id]
    assert [submission.id for submission in all_by_problem] == [second.id, first.id]


@pytest.mark.asyncio
async def test_list_submissions_verdict_filters_use_latest_judgment(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
    team_user: User,
    contest_problem: Problem,
) -> None:
    """Verdict filters inspect only the newest judgment for each submission."""
    language = await _make_language(session)
    submission, _ = await _make_submission(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        created_at=_BASE_TIME,
        autojudge_verdict=Verdict.AC,
        final_verdict=Verdict.AC,
    )
    latest_judgment = SubmissionJudgment(
        submission_id=submission.id,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.WA,
        final_verdict=Verdict.PE,
        created_at=_BASE_TIME + timedelta(minutes=1),
    )
    session.add(latest_judgment)
    await session.flush()
    await session.execute(
        update(submission_judgments_table)
        .where(submission_judgments_table.c.id == latest_judgment.id)
        .values(final_verdict=Verdict.PE.value)
    )

    old_verdict = await list_submissions(
        session,
        running_contest,
        uberadmin,
        filters=SubmissionFilters(autojudge_verdict=Verdict.AC),
    )
    latest_verdict = await list_submissions(
        session,
        running_contest,
        uberadmin,
        filters=SubmissionFilters(
            autojudge_verdict=Verdict.WA,
            final_verdict=Verdict.PE,
        ),
    )

    assert old_verdict == []
    assert [item.id for item in latest_verdict] == [submission.id]


@pytest.mark.asyncio
async def test_list_submission_teams_excludes_teams_without_submissions(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    another_team_user: User,
    contest_problem: Problem,
) -> None:
    """The Team filter options retain the existing submitted-team scope."""
    language = await _make_language(session)
    await _make_submission(
        session,
        problem=contest_problem,
        team=team_user,
        language=language,
        created_at=_BASE_TIME,
        autojudge_verdict=Verdict.AC,
        final_verdict=Verdict.AC,
    )

    teams = await list_submission_teams(session, running_contest)

    assert [team.id for team in teams] == [team_user.id]
    assert another_team_user.id not in {team.id for team in teams}
