#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reusable database fixture for the IX InterIF 2026 local contest."""

from __future__ import annotations

import csv
import hashlib
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import JudgmentStatus, RoleEnum, Verdict
from shared.language_registry import default_language_seed_rows
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem
from web.models.site import Site
from web.models.submission import Submission, SubmissionJudgment
from web.models.users import UberAdmin, User

_DATA_DIRECTORY = Path(__file__).parents[1] / "web" / "fixtures"
_SUBMISSIONS_PATH = _DATA_DIRECTORY / "interif_2026_submissions.txt"
_SCOREBOARD_PATH = _DATA_DIRECTORY / "interif_2026_scoreboard.txt"
_CONTEST_START = datetime(2026, 6, 20, 13, 0, tzinfo=UTC)
_SUBMISSION_LANGUAGE_IDS = ("python3", "rust", "lua")
_SUBMISSION_LANGUAGE_SEED = 20260620
_SOURCE_COMMENT_PREFIXES = {
    "python3": "#",
    "rust": "//",
    "lua": "--",
}
_PROBLEM_COLORS = (
    "#f44336",
    "#ff9800",
    "#ffeb3b",
    "#4caf50",
    "#00bcd4",
    "#2196f3",
    "#3f51b5",
    "#9c27b0",
    "#e91e63",
    "#795548",
)


@dataclass(frozen=True, slots=True)
class ExpectedStanding:
    """One row transcribed from the official final scoreboard."""

    final_position: int
    team: str
    site: str
    fullname: str
    problems_solved: int
    total_time_minutes: int


@dataclass(frozen=True, slots=True)
class InterIF2026ContestFixture:
    """Entities and ground truth created by :func:`load_interif_2026_contest`."""

    contest: Contest
    teams: tuple[User, ...]
    sites: tuple[Site, ...]
    problems: tuple[Problem, ...]
    submissions: tuple[Submission, ...]
    expected_standings: tuple[ExpectedStanding, ...]


@dataclass(frozen=True, slots=True)
class _SubmissionRow:
    """One normalized submission row from the BOCA export."""

    team: str
    problem: str
    submission_time_minutes: int
    verdict: str


def _fixture_id(kind: str, key: str) -> str:
    """Return a stable UUID for a fixture entity."""
    return str(uuid5(NAMESPACE_URL, f"noca:interif-2026:{kind}:{key}"))


def _read_tsv(path: Path) -> csv.DictReader[str]:
    """Open a comment-prefixed TSV fixture and return its dictionary reader."""
    lines = (line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("#"))
    return csv.DictReader(lines, delimiter="\t")


def _read_expected_standings() -> tuple[ExpectedStanding, ...]:
    """Load the official final-scoreboard rows."""
    return tuple(
        ExpectedStanding(
            final_position=int(row["final_position"]),
            team=row["team"],
            site=row["site"],
            fullname=row["fullname"],
            problems_solved=int(row["problems_solved"]),
            total_time_minutes=int(row["total_time_minutes"]),
        )
        for row in _read_tsv(_SCOREBOARD_PATH)
    )


def _read_submissions() -> tuple[_SubmissionRow, ...]:
    """Load submissions in their deterministic BOCA chronology."""
    return tuple(
        _SubmissionRow(
            team=row["team"],
            problem=row["problem"],
            submission_time_minutes=int(row["submission_time_minutes"]),
            verdict=row["verdict"],
        )
        for row in _read_tsv(_SUBMISSIONS_PATH)
    )


def _verdict(raw_verdict: str) -> tuple[JudgmentStatus, Verdict | None]:
    """Map a normalized BOCA outcome to the closest NOCA judgment."""
    if raw_verdict == "CONTACT_STAFF":
        return JudgmentStatus.FAILED, None
    mapping = {
        "ACCEPTED": Verdict.AC,
        "WRONG_ANSWER": Verdict.WA,
        "RUNTIME_ERROR": Verdict.RE,
        "COMPILATION_ERROR": Verdict.CE,
        "NAME_MISMATCH": Verdict.CE,
        "TIME_LIMIT_EXCEEDED": Verdict.TLE,
    }
    return JudgmentStatus.DONE, mapping[raw_verdict]


async def _load_submission_languages(session: AsyncSession) -> tuple[Language, ...]:
    """Load or create the real languages used by the historical submissions."""
    seed_rows = {str(row["id"]): row for row in default_language_seed_rows() if row["id"] in _SUBMISSION_LANGUAGE_IDS}
    languages: list[Language] = []
    for language_id in _SUBMISSION_LANGUAGE_IDS:
        language = await session.get(Language, language_id)
        if language is None:
            language = Language(**seed_rows[language_id])
            session.add(language)
        languages.append(language)
    return tuple(languages)


async def load_interif_2026_contest(
    session: AsyncSession,
    uberadmin: UberAdmin,
) -> InterIF2026ContestFixture:
    """Populate a full, scoreboard-verified contest in the test database.

    The fixture contains all 192 official teams, 23 sites, ten problems, and
    1,377 team submissions. The three ``judgeif`` administrative runs from the
    source HTML are intentionally excluded.

    Args:
        session: Empty test database session to populate.
        uberadmin: Fixture owner for the contest and teams.

    Returns:
        The inserted entities plus official final-scoreboard expectations.
    """
    expected_standings = _read_expected_standings()
    submission_rows = _read_submissions()
    languages = await _load_submission_languages(session)

    contest = Contest(
        id=_fixture_id("contest", "local"),
        contest_name="IX InterIF 2026 - Fase Local",
        contest_url="https://example.test/interif-2026",
        login_slug="ix-interif-2026-fase-local",
        start_time=_CONTEST_START,
        duration_minutes=180,
        stop_answers_after=180,
        stop_updating_scoreboard=120,
        clarifications_timeout_minutes=10,
        wa_penalty=20,
        accept_pe=False,
        ce_adds_penalty=False,
        created_by_uberadmin_id=uberadmin.id,
        allowed_languages=list(languages),
    )
    session.add(contest)
    await session.flush()

    site_codes = sorted({standing.site for standing in expected_standings})
    sites = tuple(
        Site(
            id=_fixture_id("site", site_code),
            sitename=f"IFSP - {site_code}",
            sitename_normalized=f"ifsp - {site_code.lower()}",
            contest_id=contest.id,
        )
        for site_code in site_codes
    )
    site_ids = {site_code: _fixture_id("site", site_code) for site_code in site_codes}
    session.add_all(sites)
    await session.flush()

    teams = tuple(
        User(
            id=_fixture_id("team", standing.team),
            username=standing.team,
            fullname=standing.fullname,
            password_hash="fixture-not-for-login",
            role=RoleEnum.TEAM,
            site_id=site_ids[standing.site],
            contest_id=contest.id,
            created_by_uberadmin_id=uberadmin.id,
        )
        for standing in expected_standings
    )
    team_ids = {team.username: team.id for team in teams}

    problems = tuple(
        Problem(
            id=_fixture_id("problem", letter),
            contest_id=contest.id,
            title=f"Problem {letter}",
            ordinal=ordinal,
            color=_PROBLEM_COLORS[ordinal - 1],
        )
        for ordinal, letter in enumerate("ABCDEFGHIJ", start=1)
    )
    problem_ids = {letter: _fixture_id("problem", letter) for letter in "ABCDEFGHIJ"}

    session.add_all([*teams, *problems])
    await session.flush()

    submissions: list[Submission] = []
    judgments: list[SubmissionJudgment] = []
    judgment_ids_by_verdict: dict[Verdict, list[str]] = {}
    language_rng = random.Random(_SUBMISSION_LANGUAGE_SEED)
    for sequence, row in enumerate(submission_rows, start=1):
        submission_id = _fixture_id("submission", str(sequence))
        language = language_rng.choice(languages)
        source_code = f"{_SOURCE_COMMENT_PREFIXES[language.id]} BOCA fixture submission {sequence}\n"
        created_at = _CONTEST_START + timedelta(
            minutes=row.submission_time_minutes,
            microseconds=sequence,
        )
        submission = Submission(
            id=submission_id,
            problem_id=problem_ids[row.problem],
            team_id=team_ids[row.team],
            language_id=language.id,
            source_code=source_code,
            source_hash=hashlib.sha256(source_code.encode()).hexdigest(),
            source_size_bytes=len(source_code.encode()),
            timestamp_seconds=row.submission_time_minutes * 60,
            created_at=created_at,
        )
        status, verdict = _verdict(row.verdict)
        judgment = SubmissionJudgment(
            id=_fixture_id("judgment", str(sequence)),
            submission_id=submission_id,
            status=status,
            autojudge_verdict=verdict,
            final_verdict=verdict,
            created_at=created_at,
            timestamp_seconds=row.submission_time_minutes * 60,
            error_message="BOCA requested contact with staff" if status == JudgmentStatus.FAILED else None,
        )
        submissions.append(submission)
        judgments.append(judgment)
        if verdict is not None:
            judgment_ids_by_verdict.setdefault(verdict, []).append(judgment.id)

    session.add_all([*submissions, *judgments])
    await session.flush()
    for verdict, judgment_ids in judgment_ids_by_verdict.items():
        await session.execute(
            update(SubmissionJudgment).where(SubmissionJudgment.id.in_(judgment_ids)).values(final_verdict=verdict)
        )
    await session.flush()
    return InterIF2026ContestFixture(
        contest=contest,
        teams=teams,
        sites=sites,
        problems=problems,
        submissions=tuple(submissions),
        expected_standings=expected_standings,
    )
