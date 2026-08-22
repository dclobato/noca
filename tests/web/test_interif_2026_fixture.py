#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Integrity tests for the IX InterIF 2026 full-contest fixture."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from werkzeug.security import check_password_hash

from scripts.web.seed_interif_2026 import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    FIXTURE_LANGUAGE_ID,
    JUDGE_PASSWORD,
    InterIF2026SeedError,
    remove_interif_2026,
    seed_interif_2026,
)
from shared.enumerations import JudgmentStatus, RoleEnum, TaskType, Verdict

try:
    from shared.services.scoreboard_projection import compute_icpc
except ImportError:  # pragma: no cover - compatibility with pre-animator master
    from web.services.scoreboard.computation import compute_icpc  # type: ignore[no-redef,unused-ignore]
from tests.fixtures.interif_2026 import InterIF2026ContestFixture
from web.config import settings
from web.database import Base
from web.models.clarification import Clarification
from web.models.contest import Contest, Task
from web.models.language import Language
from web.models.submission import Submission, SubmissionJudgment
from web.models.users import UberAdmin, User
from web.services.contest_backup_service import build_contest_backup, import_contest_backup


async def test_interif_2026_fixture_has_the_official_contest_shape(
    interif_2026_contest_fixture: InterIF2026ContestFixture,
) -> None:
    """The fixture includes every official team submission and no judge runs."""
    fixture = interif_2026_contest_fixture

    assert len(fixture.sites) == 22
    assert len(fixture.teams) == 192
    assert len(fixture.problems) == 10
    assert len(fixture.submissions) == 1_377
    assert len(fixture.expected_standings) == 192
    assert {language.id for language in fixture.contest.allowed_languages} == {"python3", "rust", "lua"}
    assert {submission.language_id for submission in fixture.submissions} == {"python3", "rust", "lua"}
    assert {team.username for team in fixture.teams}.isdisjoint({"judgeif"})

    chronology = [
        (submission.timestamp_seconds, submission.created_at, submission.id) for submission in fixture.submissions
    ]
    assert chronology == sorted(chronology)


async def test_interif_2026_fixture_preserves_boca_outcome_distribution(
    session: AsyncSession,
    interif_2026_contest_fixture: InterIF2026ContestFixture,
) -> None:
    """Normalized judgments retain all official BOCA outcome categories."""
    fixture = interif_2026_contest_fixture
    judgment_rows = (
        (
            await session.execute(
                select(SubmissionJudgment).where(
                    SubmissionJudgment.submission_id.in_(submission.id for submission in fixture.submissions)
                )
            )
        )
        .scalars()
        .all()
    )

    assert Counter(judgment.status for judgment in judgment_rows) == {
        JudgmentStatus.DONE: 1_376,
        JudgmentStatus.FAILED: 1,
    }
    assert Counter(judgment.final_verdict for judgment in judgment_rows) == {
        Verdict.AC: 493,
        Verdict.WA: 760,
        Verdict.RE: 69,
        Verdict.CE: 45,
        Verdict.TLE: 9,
        None: 1,
    }


async def test_interif_2026_fixture_matches_the_official_final_scoreboard(
    session: AsyncSession,
    interif_2026_contest_fixture: InterIF2026ContestFixture,
) -> None:
    """Shared ICPC projection reproduces every official solved/time total."""
    fixture = interif_2026_contest_fixture
    judgment_rows = (
        (
            await session.execute(
                select(SubmissionJudgment).where(
                    SubmissionJudgment.submission_id.in_(submission.id for submission in fixture.submissions)
                )
            )
        )
        .scalars()
        .all()
    )
    judgments = {judgment.submission_id: judgment for judgment in judgment_rows}

    standings = compute_icpc(
        contest=fixture.contest,
        teams=fixture.teams,
        problems=fixture.problems,
        submissions=fixture.submissions,
        judgments=judgments,
        freeze_at_seconds=fixture.contest.duration_minutes * 60,
        viewer_sees_frozen=False,
    )
    actual_totals = {standing.team_name: (standing.problems_solved, standing.total_time) for standing in standings}
    expected_totals = {
        standing.team: (standing.problems_solved, standing.total_time_minutes)
        for standing in fixture.expected_standings
    }

    assert actual_totals == expected_totals


async def test_seed_interif_2026_creates_finished_animator_contest_and_admin(
    session: AsyncSession,
    uberadmin: UberAdmin,
) -> None:
    """The development seed creates an accessible, finished contest."""
    result = await seed_interif_2026(session, uberadmin)
    contest = result.fixture.contest
    admin = result.admin

    assert contest.is_past is True
    assert contest.active is True
    assert contest.release_scoreboard_after_end is True
    assert contest.release_problem_set_after_end is True
    assert contest.owner_user_id == admin.id
    assert contest.chief_judge_id == result.judge.id
    assert admin.username == ADMIN_USERNAME
    assert admin.role == RoleEnum.ADMIN
    assert check_password_hash(admin.password_hash, ADMIN_PASSWORD)
    assert result.judge.username == "judgeif"
    assert result.judge.role == RoleEnum.JUDGE
    assert check_password_hash(result.judge.password_hash, JUDGE_PASSWORD)
    for problem in result.fixture.problems:
        statement_path = settings.PROBLEM_STATEMENT_DIR / f"{problem.id}-statement.md"
        assert statement_path.read_text(encoding="utf-8").startswith(f"# {problem.title}\n")

    clarifications = tuple((await session.scalars(select(Clarification))).all())
    assert len(clarifications) == 23
    assert all(clarification.judge_id == result.judge.id for clarification in clarifications)
    assert all(clarification.answer and clarification.answered_at for clarification in clarifications)
    general_clarifications = tuple(
        clarification for clarification in clarifications if clarification.problem_id is None
    )
    assert len(general_clarifications) == 8
    assert all(not clarification.question.startswith("[General]") for clarification in general_clarifications)

    tasks = tuple((await session.scalars(select(Task))).all())
    assert len(tasks) == 491
    assert Counter(task.type for task in tasks) == {
        TaskType.FIRST_BALLOON: 7,
        TaskType.BALLOON: 484,
    }
    assert all(task.staff_id == admin.id for task in tasks)
    assert all(task.finished_at is not None for task in tasks)
    assert all(task.finished_timestamp_seconds == task.created_timestamp_seconds + 1 for task in tasks)


async def test_seed_interif_2026_refuses_to_overwrite_existing_contest(
    session: AsyncSession,
    uberadmin: UberAdmin,
) -> None:
    """A second seed attempt fails instead of mutating existing E2E data."""
    await seed_interif_2026(session, uberadmin)

    with pytest.raises(InterIF2026SeedError, match="already exists"):
        await seed_interif_2026(session, uberadmin)


async def test_seed_interif_2026_supports_backup_restore(
    session: AsyncSession,
    uberadmin: UberAdmin,
    tmp_path: Path,
) -> None:
    """The development seed can complete a full backup and restore round trip."""
    result = await seed_interif_2026(session, uberadmin)
    await session.commit()
    backup_path = tmp_path / "interif-2026.zip"

    await build_contest_backup(
        session,
        result.fixture.contest,
        backup_path,
        include_password_hashes=False,
        include_media=False,
    )
    restored = await import_contest_backup(
        session,
        backup_path,
        actor_uberadmin=uberadmin,
        new_name="IX InterIF 2026 - Restored",
        new_slug="ix-interif-2026-restored",
        testcase_dir=settings.PROBLEM_TESTCASE_DIR,
        statement_dir=settings.PROBLEM_STATEMENT_DIR,
    )

    assert restored.problem_count == 10
    assert restored.user_count == 194
    assert restored.submission_count == 1_377


async def test_remove_interif_2026_deletes_only_the_seeded_contest(
    session: AsyncSession,
    uberadmin: UberAdmin,
) -> None:
    """The removal mode deletes seeded contest data and is idempotent."""
    result = await seed_interif_2026(session, uberadmin)
    contest_id = result.fixture.contest.id
    submission_ids = tuple(submission.id for submission in result.fixture.submissions)
    statement_paths = tuple(
        settings.PROBLEM_STATEMENT_DIR / f"{problem.id}-statement.md" for problem in result.fixture.problems
    )

    assert await remove_interif_2026(session) is True
    assert await session.scalar(select(Contest.id).where(Contest.id == contest_id)) is None
    assert await session.scalar(select(User.id).where(User.contest_id == contest_id)) is None
    assert await session.scalar(select(Submission.id).where(Submission.id.in_(submission_ids))) is None
    assert await session.get(Language, FIXTURE_LANGUAGE_ID) is None
    assert all(not statement_path.exists() for statement_path in statement_paths)
    assert await remove_interif_2026(session) is False


async def test_seed_interif_2026_respects_foreign_key_insert_order(tmp_path: Path) -> None:
    """The seed succeeds when SQLite enforces PostgreSQL-like foreign keys."""
    database_path = tmp_path / "interif-foreign-keys.sqlite3"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            uberadmin = UberAdmin(
                username="fk_seed_owner",
                fullname="Foreign Key Seed Owner",
                email_normalizado="fk-seed-owner@example.test",
            )
            uberadmin.password = "StrongPasswd1!"
            session.add(uberadmin)
            await session.flush()

            result = await seed_interif_2026(session, uberadmin)
            contest_id = result.fixture.contest.id

            assert len(result.fixture.teams) == 192
            assert len(result.fixture.submissions) == 1_377
            assert await remove_interif_2026(session) is True
            assert await session.scalar(select(Contest.id).where(Contest.id == contest_id)) is None
    finally:
        await engine.dispose()
