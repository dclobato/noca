#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the public contest live submission feed snapshot."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import submission_judgments as sj_table
from shared.enumerations import JudgmentStatus, RoleEnum, Verdict
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem
from web.models.submission import Submission, SubmissionJudgment
from web.models.users import User
from web.services.live_feed_service import build_contest_live_feed_snapshot

_BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


async def _make_language(session: AsyncSession) -> Language:
    language = Language(
        id=f"lf-{uuid4().hex[:8]}",
        name="Live Feed Language",
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


async def _make_team(
    session: AsyncSession, contest: Contest, uberadmin_id: str, name: str, role: RoleEnum = RoleEnum.TEAM
) -> User:
    user = User(
        username=f"{name}-{uuid4().hex[:6]}",
        fullname=name,
        role=role,
        contest_id=contest.id,
        created_by_uberadmin_id=uberadmin_id,
    )
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


async def _make_submission(
    session: AsyncSession,
    *,
    problem: Problem,
    team: User,
    language: Language,
    created_at: datetime,
    timestamp_minutes: int,
    status: JudgmentStatus,
    final_verdict: Verdict | None,
) -> Submission:
    source = f"print('{uuid4().hex}')\n"
    submission = Submission(
        problem_id=problem.id,
        team_id=team.id,
        language_id=language.id,
        source_code=source,
        source_hash=hashlib.sha256(source.encode()).hexdigest(),
        source_size_bytes=len(source.encode()),
        timestamp_seconds=timestamp_minutes * 60,
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(submission)
    await session.flush()

    judgment = SubmissionJudgment(
        submission_id=submission.id,
        status=status,
        autojudge_verdict=final_verdict,
        final_verdict=final_verdict,
        created_at=created_at,
        timestamp_seconds=timestamp_minutes * 60,
    )
    session.add(judgment)
    await session.flush()

    # The before_flush hook recomputes final_verdict from confirmations (None for new
    # judgments). Persist the intended value directly so the DB reflects the setup.
    if final_verdict is not None:
        await session.execute(
            update(sj_table).where(sj_table.c.id == judgment.id).values(final_verdict=final_verdict.value)
        )
    return submission


@pytest.mark.asyncio
async def test_live_feed_lists_finalized_team_submissions(
    session: AsyncSession, running_contest: Contest, uberadmin, contest_problem: Problem
) -> None:
    """Only finalized team submissions appear, newest first, with real verdicts."""
    language = await _make_language(session)
    team = await _make_team(session, running_contest, uberadmin.id, "Team Alpha")

    first = await _make_submission(
        session,
        problem=contest_problem,
        team=team,
        language=language,
        created_at=_BASE,
        timestamp_minutes=5,
        status=JudgmentStatus.DONE,
        final_verdict=Verdict.WA,
    )
    second = await _make_submission(
        session,
        problem=contest_problem,
        team=team,
        language=language,
        created_at=_BASE + timedelta(minutes=5),
        timestamp_minutes=10,
        status=JudgmentStatus.DONE,
        final_verdict=Verdict.AC,
    )
    # Not finalized yet — excluded.
    await _make_submission(
        session,
        problem=contest_problem,
        team=team,
        language=language,
        created_at=_BASE + timedelta(minutes=10),
        timestamp_minutes=15,
        status=JudgmentStatus.JUDGING,
        final_verdict=None,
    )

    rows = (await build_contest_live_feed_snapshot(session, running_contest)).rows

    assert [row.submission_id for row in rows] == [second.id, first.id]
    assert rows[0].verdict == "AC"
    assert rows[0].team == "Team Alpha"
    assert rows[0].problem_label == "A"
    assert rows[0].language_icon == "devicon-python-plain"
    assert all(not row.frozen for row in rows)


@pytest.mark.asyncio
async def test_live_feed_blackout_anonymizes_team_and_verdict(
    session: AsyncSession, running_contest: Contest, uberadmin, contest_problem: Problem
) -> None:
    """After freeze, post-freeze rows hide both team identity and verdict."""
    # Freeze the scoreboard after 1 minute; the contest started 30 minutes ago.
    running_contest.stop_updating_scoreboard = 1
    await session.flush()
    assert running_contest.is_scoreboard_frozen is True

    language = await _make_language(session)
    team = await _make_team(session, running_contest, uberadmin.id, "Secret Team")

    # Pre-freeze submission (timestamp 0 min <= 1 min): shown in full.
    pre = await _make_submission(
        session,
        problem=contest_problem,
        team=team,
        language=language,
        created_at=_BASE,
        timestamp_minutes=0,
        status=JudgmentStatus.DONE,
        final_verdict=Verdict.AC,
    )
    # Post-freeze submission (timestamp 10 min > 1 min): anonymized.
    post = await _make_submission(
        session,
        problem=contest_problem,
        team=team,
        language=language,
        created_at=_BASE + timedelta(minutes=10),
        timestamp_minutes=10,
        status=JudgmentStatus.DONE,
        final_verdict=Verdict.AC,
    )

    rows = (await build_contest_live_feed_snapshot(session, running_contest)).rows
    by_id = {row.submission_id: row for row in rows}

    assert by_id[pre.id].frozen is False
    assert by_id[pre.id].team == "Secret Team"
    assert by_id[pre.id].verdict == "AC"

    assert by_id[post.id].frozen is True
    assert by_id[post.id].team == "—"
    assert by_id[post.id].verdict is None
    assert by_id[post.id].verdict_badge_class == "bg-secondary"
    # Problem/language remain visible on frozen rows.
    assert by_id[post.id].problem_label == "A"
    assert by_id[post.id].language_name == "Live Feed Language"
