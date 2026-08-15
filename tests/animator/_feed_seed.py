#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared seed helpers for the animator feed tests.

Seed data is written through the Web ORM, except judgments, which are inserted
via Core to mirror how the autojudge writes verdicts (a Core insert bypasses the
Web ORM ``before_flush`` hook that would otherwise recompute ``final_verdict``
from human confirmations).

The module name is underscore-prefixed so pytest does not collect it as a test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from shared.db_schema import submission_judgments as sj_table
from shared.db_schema import submissions as submissions_table
from shared.enumerations import JudgmentStatus, ProblemValidatorType, RoleEnum, Verdict
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem
from web.models.site import Site
from web.models.submission import Submission
from web.models.users import UberAdmin, User

START = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def feed_session(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a fresh animator-style session factory on the given engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def make_language(session: AsyncSession) -> Language:
    """Create and flush a minimal Language row for FK satisfaction."""
    lang = Language(
        id=f"lang-{uuid.uuid4().hex[:6]}",
        name="Test Language",
        icon="test",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="sol.txt",
        artifact_path="/sandbox/sol.txt",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(lang)
    await session.flush()
    return lang


async def make_contest(
    session: AsyncSession,
    uberadmin: UberAdmin,
    *,
    slug: str | None = None,
    animator_enabled: bool = True,
    stop_updating_scoreboard: int = 240,
    accept_pe: bool = False,
    ce_adds_penalty: bool = False,
    wa_penalty: int = 20,
    release_scoreboard_after_end: bool = False,
) -> Contest:
    """Create and flush a contest with the given scoring options."""
    contest = Contest(
        contest_name="Feed Contest",
        contest_url="http://feed.example.com",
        login_slug=slug or f"feed-{uuid.uuid4().hex[:8]}",
        start_time=START,
        duration_minutes=300,
        stop_answers_after=300,
        stop_updating_scoreboard=stop_updating_scoreboard,
        clarifications_timeout_minutes=10,
        wa_penalty=wa_penalty,
        accept_pe=accept_pe,
        ce_adds_penalty=ce_adds_penalty,
        release_scoreboard_after_end=release_scoreboard_after_end,
        animator_enabled=animator_enabled,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(contest)
    await session.flush()
    return contest


def make_user(
    contest: Contest,
    uberadmin: UberAdmin,
    username: str,
    *,
    site_id: str | None = None,
    role: RoleEnum = RoleEnum.TEAM,
) -> User:
    """Build (without adding) a contest user with the given role."""
    user = User(
        username=username,
        fullname=f"Full {username}",
        role=role,
        contest_id=contest.id,
        site_id=site_id,
        created_by_uberadmin_id=uberadmin.id,
    )
    user.password = "TestPass1!"
    return user


def make_problem(contest: Contest, ordinal: int, color: str = "#ff0000") -> Problem:
    """Build (without adding) a contest problem."""
    return Problem(
        contest_id=contest.id,
        title=f"Problem {ordinal}",
        ordinal=ordinal,
        color=color,
        validator_type=ProblemValidatorType.STANDARD,
    )


async def make_site(
    session: AsyncSession,
    contest: Contest,
    *,
    sitename: str = "Campus A",
    gold: int = 1,
    silver: int = 2,
    bronze: int = 3,
) -> Site:
    """Create and flush a contest site with medal cutoffs."""
    site = Site(
        sitename=sitename,
        sitename_normalized=sitename.lower(),
        contest_id=contest.id,
        gold_cutoff=gold,
        silver_cutoff=silver,
        bronze_cutoff=bronze,
    )
    session.add(site)
    await session.flush()
    return site


async def add_judgment(
    session: AsyncSession,
    *,
    submission_id: str,
    status: JudgmentStatus,
    verdict: Verdict | None,
    created_at: datetime,
    timestamp_seconds: int,
) -> None:
    """Insert a judgment via Core, mirroring how the autojudge writes verdicts."""
    await session.execute(
        insert(sj_table).values(
            id=uuid.uuid4().hex,
            submission_id=submission_id,
            status=status,
            autojudge_verdict=verdict if status == JudgmentStatus.DONE else None,
            final_verdict=verdict,
            created_at=created_at,
            timestamp_seconds=timestamp_seconds,
        )
    )


async def add_submission(
    session: AsyncSession,
    *,
    problem: Problem,
    team: User,
    language: Language,
    minutes: int,
    verdict: Verdict | None,
    status: JudgmentStatus = JudgmentStatus.DONE,
    created_offset_s: int = 0,
) -> Submission:
    """Create a submission (via ORM) and its judgment (via Core)."""
    sub = Submission(
        problem_id=problem.id,
        team_id=team.id,
        language_id=language.id,
        source_code="x",
        source_hash=uuid.uuid4().hex,
        source_size_bytes=1,
        timestamp_seconds=minutes * 60,
    )
    session.add(sub)
    await session.flush()
    await add_judgment(
        session,
        submission_id=sub.id,
        status=status,
        verdict=verdict,
        created_at=START + timedelta(minutes=minutes, seconds=created_offset_s),
        timestamp_seconds=minutes * 60,
    )
    return sub


async def add_submission_core(
    session: AsyncSession,
    *,
    problem: Problem,
    team_id: str,
    language: Language,
    minutes: int,
    verdict: Verdict | None,
    status: JudgmentStatus = JudgmentStatus.DONE,
) -> str:
    """Insert a submission via Core, bypassing the Web ORM TEAM-role invariant.

    Used to construct adversarial rows (e.g. a non-team or cross-contest user's
    submission on a contest problem) that the ORM would refuse but that the
    animator's Core read path must still handle safely.
    """
    submission_id = uuid.uuid4().hex
    now = START + timedelta(minutes=minutes)
    await session.execute(
        insert(submissions_table).values(
            id=submission_id,
            problem_id=problem.id,
            team_id=team_id,
            language_id=language.id,
            source_code="x",
            source_hash=uuid.uuid4().hex,
            source_size_bytes=1,
            timestamp_seconds=minutes * 60,
            created_at=now,
            updated_at=now,
        )
    )
    await add_judgment(
        session,
        submission_id=submission_id,
        status=status,
        verdict=verdict,
        created_at=now,
        timestamp_seconds=minutes * 60,
    )
    return submission_id


async def seed_dataset(
    session: AsyncSession,
    contest: Contest,
    uberadmin: UberAdmin,
    *,
    teams: int,
    problems: int,
    submissions: int,
) -> None:
    """Seed a contest with a site, teams, problems, and DONE-judged submissions."""
    language = await make_language(session)
    site = await make_site(session, contest, sitename="Campus")

    team_rows = [make_user(contest, uberadmin, f"team{index}", site_id=site.id) for index in range(teams)]
    problem_rows = [make_problem(contest, ordinal) for ordinal in range(1, problems + 1)]
    session.add_all([*team_rows, *problem_rows])
    await session.flush()

    for index in range(submissions):
        await add_submission(
            session,
            problem=problem_rows[index % len(problem_rows)],
            team=team_rows[index % len(team_rows)],
            language=language,
            minutes=index + 1,
            verdict=Verdict.AC if index % 2 == 0 else Verdict.WA,
        )
