"""
tests/test_animeitor_export.py

Unit and integration tests for web/services/animeitor_export_service.py.

Pure unit tests cover verdict mapping, contest/runs serialization, and
file content. Integration tests use the DB session fixture to build full
contest scenarios and verify the ZIP output.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import JudgmentStatus, RoleEnum, Verdict
from shared.timing import icpc_minutes_from_seconds
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem
from web.models.submission import Submission, SubmissionJudgment
from web.models.users import UberAdmin, User
from web.services.animeitor_export_service import (
    FS,
    AnimeitorExportError,
    AnimeitorRun,
    AnimeitorTeam,
    build_animeitor_zip,
    map_verdict,
    serialize_contest_file,
    serialize_runs_file,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_language(session: AsyncSession) -> Language:
    language = Language(
        id="python3",
        name="Python 3",
        icon="python",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["python3", "-m", "py_compile", "/sandbox/main.py"],
        run_cmd=["python3", "-u", "/sandbox/main.py"],
        source_filename="main.py",
        artifact_path="/sandbox/main.py",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(language)
    await session.flush()
    return language


async def _make_team(
    session: AsyncSession,
    contest: Contest,
    uberadmin: UberAdmin,
    username: str,
    fullname: str,
) -> User:
    user = User(
        username=username,
        fullname=fullname,
        role=RoleEnum.TEAM,
        contest_id=contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


async def _make_problem(
    session: AsyncSession,
    contest: Contest,
    title: str,
    ordinal: int,
) -> Problem:
    problem = Problem(
        contest_id=contest.id,
        title=title,
        ordinal=ordinal,
        color="#ff0000",
    )
    session.add(problem)
    await session.flush()
    return problem


async def _make_submission(
    session: AsyncSession,
    problem: Problem,
    team: User,
    language: Language,
    timestamp_seconds: int,
    final_verdict: Verdict | None = None,
    judgment_status: JudgmentStatus = JudgmentStatus.DONE,
    source_code: str | None = None,
    created_at: datetime | None = None,
) -> Submission:
    code = source_code or f"print('hello {timestamp_seconds}')"
    submission = Submission(
        problem_id=problem.id,
        team_id=team.id,
        language_id=language.id,
        source_code=code,
        source_hash=hashlib.sha256(code.encode("utf-8")).hexdigest(),
        source_size_bytes=len(code.encode("utf-8")),
        timestamp_seconds=timestamp_seconds,
        created_at=created_at or datetime.now(UTC),
    )
    session.add(submission)
    await session.flush()

    judgment = SubmissionJudgment(
        submission_id=submission.id,
        status=judgment_status,
        autojudge_verdict=final_verdict,
        final_verdict=final_verdict,
        created_at=datetime.now(UTC),
        timestamp_seconds=timestamp_seconds,
    )
    session.add(judgment)
    await session.flush()

    # ORM flush hooks may override final_verdict via _derive_final_verdict.
    # Force the intended value with a raw UPDATE, same pattern as
    # test_team_submission_export.py.
    if final_verdict is not None:
        await session.execute(
            update(SubmissionJudgment).where(SubmissionJudgment.id == judgment.id).values(final_verdict=final_verdict)
        )
        await session.flush()

    return submission


def _read_zip(zip_bytes: bytes) -> dict[str, str]:
    """Extract all files from a ZIP into a dict of name → content."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}


# ===========================================================================
# Pure unit tests — verdict mapping
# ===========================================================================


class TestMapVerdict:
    def test_ac(self) -> None:
        assert map_verdict(Verdict.AC, accept_pe=False) == "Y"
        assert map_verdict(Verdict.AC, accept_pe=True) == "Y"

    @pytest.mark.parametrize("verdict", [Verdict.WA, Verdict.RE, Verdict.TLE, Verdict.MLE, Verdict.OLE])
    def test_wrong_answers(self, verdict: Verdict) -> None:
        assert map_verdict(verdict, accept_pe=False) == "N"
        assert map_verdict(verdict, accept_pe=True) == "N"

    def test_ce(self) -> None:
        assert map_verdict(Verdict.CE, accept_pe=False) == "X"
        assert map_verdict(Verdict.CE, accept_pe=True) == "X"

    def test_pe_accepted(self) -> None:
        assert map_verdict(Verdict.PE, accept_pe=True) == "Y"

    def test_pe_rejected(self) -> None:
        assert map_verdict(Verdict.PE, accept_pe=False) == "N"

    def test_none(self) -> None:
        assert map_verdict(None, accept_pe=False) == "?"
        assert map_verdict(None, accept_pe=True) == "?"


# ===========================================================================
# Pure unit tests — contest file serialization
# ===========================================================================


class TestSerializeContestFile:
    def test_structure(self) -> None:
        teams = [
            AnimeitorTeam("team01", "USP", "Alpha"),
            AnimeitorTeam("team02", "UNICAMP", "Beta"),
            AnimeitorTeam("team03", "UFMG", "Gamma"),
        ]
        result = serialize_contest_file(
            contest_name="Test Contest",
            max_time_minutes=300,
            current_time_minutes=120,
            freeze_time_minutes=240,
            penalty=20,
            teams=teams,
            num_problems=5,
        )
        lines = result.strip().split("\n")

        # Line count: 1 header + 1 timing + 1 counts + 3 teams + 2 footer = 8
        assert len(lines) == 8

        # Line 1: contest name
        assert lines[0] == "Test Contest"

        # Line 2: timing fields
        fields = lines[1].split(FS)
        assert fields == ["300", "120", "240", "20"]

        # Line 3: counts
        counts = lines[2].split(FS)
        assert counts == ["3", "5"]

        # Lines 4-6: teams
        for i, team in enumerate(teams):
            team_fields = lines[3 + i].split(FS)
            assert team_fields == [team.login, team.institution, team.display_name]

        # Penultimate: 1 FS 1
        assert lines[6].split(FS) == ["1", "1"]

        # Last: numProblems FS Y
        assert lines[7].split(FS) == ["5", "Y"]

    def test_single_team_single_problem(self) -> None:
        teams = [AnimeitorTeam("solo", "MIT", "Solo Team")]
        result = serialize_contest_file(
            contest_name="Mini",
            max_time_minutes=60,
            current_time_minutes=30,
            freeze_time_minutes=50,
            penalty=20,
            teams=teams,
            num_problems=1,
        )
        lines = result.strip().split("\n")
        # 1 header + 1 timing + 1 counts + 1 team + 2 footer = 6
        assert len(lines) == 6
        assert lines[4].split(FS) == ["1", "1"]
        assert lines[5].split(FS) == ["1", "Y"]


# ===========================================================================
# Pure unit tests — runs file serialization
# ===========================================================================


class TestSerializeRunsFile:
    def test_multiple_runs(self) -> None:
        runs = [
            AnimeitorRun(1, 15, "team01", "A", "N"),
            AnimeitorRun(2, 18, "team02", "B", "Y"),
            AnimeitorRun(3, 22, "team01", "A", "Y"),
            AnimeitorRun(4, 30, "team03", "C", "?"),
            AnimeitorRun(5, 31, "team02", "D", "X"),
        ]
        result = serialize_runs_file(runs)
        lines = result.strip().split("\n")
        assert len(lines) == 5

        for i, run in enumerate(runs):
            fields = lines[i].split(FS)
            assert fields == [
                str(run.run_id),
                str(run.time_minutes),
                run.team_login,
                run.problem_letter,
                run.status,
            ]

    def test_empty(self) -> None:
        assert serialize_runs_file([]) == ""


# ===========================================================================
# Pure unit tests — time/version/icpc file content
# ===========================================================================


class TestStaticFileContents:
    """These tests verify via the ZIP output of build_animeitor_zip.

    Since time/version/icpc are written directly as string literals in the
    orchestrator, we test them through the integration path.
    """

    @pytest.mark.asyncio
    async def test_version_file(
        self,
        session: AsyncSession,
        running_contest: Contest,
        uberadmin: UberAdmin,
    ) -> None:
        await _make_team(session, running_contest, uberadmin, "t1", "Team 1")
        await _make_problem(session, running_contest, "Problem A", 1)
        _, zip_bytes = await build_animeitor_zip(session, running_contest)
        files = _read_zip(zip_bytes)
        assert files["version"] == "1.0"

    @pytest.mark.asyncio
    async def test_icpc_file(
        self,
        session: AsyncSession,
        running_contest: Contest,
        uberadmin: UberAdmin,
    ) -> None:
        await _make_team(session, running_contest, uberadmin, "t1", "Team 1")
        await _make_problem(session, running_contest, "Problem A", 1)
        _, zip_bytes = await build_animeitor_zip(session, running_contest)
        files = _read_zip(zip_bytes)
        assert files["icpc"] == ""

    @pytest.mark.asyncio
    async def test_time_file_during_contest(
        self,
        session: AsyncSession,
        uberadmin: UberAdmin,
    ) -> None:
        """Running contest: time file should contain elapsed seconds."""
        contest = Contest(
            contest_name="Running",
            contest_url="http://test.example.com",
            login_slug="running-time-test",
            start_time=datetime.now(UTC) - timedelta(minutes=30),
            duration_minutes=120,
            stop_answers_after=120,
            stop_updating_scoreboard=100,
            clarifications_timeout_minutes=10,
            created_by_uberadmin_id=uberadmin.id,
        )
        session.add(contest)
        await session.flush()
        await _make_team(session, contest, uberadmin, "t1", "Team 1")
        await _make_problem(session, contest, "Problem A", 1)

        _, zip_bytes = await build_animeitor_zip(session, contest)
        files = _read_zip(zip_bytes)
        elapsed = int(files["time"])
        # Should be approximately 30 minutes = 1800 seconds (±5s tolerance)
        assert 1795 <= elapsed <= 1810

    @pytest.mark.asyncio
    async def test_time_file_after_contest(
        self,
        session: AsyncSession,
        uberadmin: UberAdmin,
    ) -> None:
        """Ended contest: time should be clamped to duration."""
        contest = Contest(
            contest_name="Ended",
            contest_url="http://test.example.com",
            login_slug="ended-time-test",
            start_time=datetime.now(UTC) - timedelta(hours=5),
            duration_minutes=60,
            stop_answers_after=60,
            stop_updating_scoreboard=60,
            clarifications_timeout_minutes=10,
            created_by_uberadmin_id=uberadmin.id,
        )
        session.add(contest)
        await session.flush()
        await _make_team(session, contest, uberadmin, "t1", "Team 1")
        await _make_problem(session, contest, "Problem A", 1)

        _, zip_bytes = await build_animeitor_zip(session, contest)
        files = _read_zip(zip_bytes)
        assert files["time"] == str(60 * 60)  # 3600 seconds

    @pytest.mark.asyncio
    async def test_time_file_before_contest(
        self,
        session: AsyncSession,
        uberadmin: UberAdmin,
    ) -> None:
        """Not-yet-started contest: time should be 0."""
        contest = Contest(
            contest_name="Future",
            contest_url="http://test.example.com",
            login_slug="future-time-test",
            start_time=datetime.now(UTC) + timedelta(hours=1),
            duration_minutes=120,
            stop_answers_after=120,
            stop_updating_scoreboard=100,
            clarifications_timeout_minutes=10,
            created_by_uberadmin_id=uberadmin.id,
        )
        session.add(contest)
        await session.flush()
        await _make_team(session, contest, uberadmin, "t1", "Team 1")
        await _make_problem(session, contest, "Problem A", 1)

        _, zip_bytes = await build_animeitor_zip(session, contest)
        files = _read_zip(zip_bytes)
        assert files["time"] == "0"


# ===========================================================================
# Integration tests — precondition checks
# ===========================================================================


class TestPreconditions:
    @pytest.mark.asyncio
    async def test_rejects_no_teams(
        self,
        session: AsyncSession,
        running_contest: Contest,
    ) -> None:
        await _make_problem(session, running_contest, "Problem A", 1)
        with pytest.raises(AnimeitorExportError, match="No teams enrolled"):
            await build_animeitor_zip(session, running_contest)

    @pytest.mark.asyncio
    async def test_rejects_no_problems(
        self,
        session: AsyncSession,
        running_contest: Contest,
        uberadmin: UberAdmin,
    ) -> None:
        await _make_team(session, running_contest, uberadmin, "t1", "Team 1")
        with pytest.raises(AnimeitorExportError, match="No problems defined"):
            await build_animeitor_zip(session, running_contest)


# ===========================================================================
# Integration tests — full ZIP
# ===========================================================================


@pytest_asyncio.fixture
async def _language(session: AsyncSession) -> Language:
    return await _make_language(session)


class TestBuildAnimeitorZip:
    @pytest.mark.asyncio
    async def test_full_contest(
        self,
        session: AsyncSession,
        running_contest: Contest,
        uberadmin: UberAdmin,
        _language: Language,
    ) -> None:
        """Full scenario with multiple teams, problems, and verdict types."""
        t1 = await _make_team(session, running_contest, uberadmin, "alpha", "Team Alpha")
        t2 = await _make_team(session, running_contest, uberadmin, "bravo", "Team Bravo")

        p1 = await _make_problem(session, running_contest, "Sum", 1)
        p2 = await _make_problem(session, running_contest, "Sort", 2)
        p3 = await _make_problem(session, running_contest, "Graph", 3)

        # Submissions with different verdicts
        await _make_submission(session, p1, t1, _language, 300, Verdict.AC)
        await _make_submission(session, p1, t2, _language, 600, Verdict.WA)
        await _make_submission(session, p2, t1, _language, 900, Verdict.CE)
        await _make_submission(session, p3, t2, _language, 1200, None, JudgmentStatus.JUDGING)

        filename, zip_bytes = await build_animeitor_zip(session, running_contest)

        # Filename
        assert filename.startswith("animeitor-")
        assert filename.endswith(".zip")

        # ZIP has exactly 5 entries
        files = _read_zip(zip_bytes)
        assert set(files.keys()) == {"contest", "runs", "time", "version", "icpc"}

        # --- contest file ---
        contest_lines = files["contest"].strip().split("\n")
        assert contest_lines[0] == running_contest.contest_name

        timing = contest_lines[1].split(FS)
        assert timing[0] == str(running_contest.duration_minutes)
        assert timing[2] == str(running_contest.stop_updating_scoreboard)
        assert timing[3] == "20"  # hardcoded penalty

        counts = contest_lines[2].split(FS)
        assert counts[0] == "2"  # 2 teams
        assert counts[1] == "3"  # 3 problems

        # Teams ordered by username
        team_line_1 = contest_lines[3].split(FS)
        assert team_line_1[0] == "alpha"
        team_line_2 = contest_lines[4].split(FS)
        assert team_line_2[0] == "bravo"

        # Footer
        assert contest_lines[-2].split(FS) == ["1", "1"]
        assert contest_lines[-1].split(FS) == ["3", "Y"]

        # --- runs file ---
        runs_lines = files["runs"].strip().split("\n")
        assert len(runs_lines) == 4  # 4 submissions

        # Check statuses
        statuses = [line.split(FS)[4] for line in runs_lines]
        assert statuses == ["Y", "N", "X", "?"]

        # Check monotonic IDs
        ids = [int(line.split(FS)[0]) for line in runs_lines]
        assert ids == [1, 2, 3, 4]

        # Check problem letters
        letters = [line.split(FS)[3] for line in runs_lines]
        assert letters == ["A", "A", "B", "C"]

        # Check run times use ICPC minutes
        minutes = [int(line.split(FS)[1]) for line in runs_lines]
        assert minutes == [
            icpc_minutes_from_seconds(300),
            icpc_minutes_from_seconds(600),
            icpc_minutes_from_seconds(900),
            icpc_minutes_from_seconds(1200),
        ]

        # --- time file ---
        elapsed = int(files["time"])
        assert elapsed > 0

        # --- version ---
        assert files["version"] == "1.0"

        # --- icpc ---
        assert files["icpc"] == ""

    @pytest.mark.asyncio
    async def test_no_submissions(
        self,
        session: AsyncSession,
        running_contest: Contest,
        uberadmin: UberAdmin,
    ) -> None:
        """Contest with teams and problems but no submissions produces a valid ZIP."""
        await _make_team(session, running_contest, uberadmin, "t1", "Team 1")
        await _make_problem(session, running_contest, "Problem A", 1)

        _, zip_bytes = await build_animeitor_zip(session, running_contest)
        files = _read_zip(zip_bytes)

        assert set(files.keys()) == {"contest", "runs", "time", "version", "icpc"}
        assert files["runs"] == ""

    @pytest.mark.asyncio
    async def test_pe_acceptance(
        self,
        session: AsyncSession,
        uberadmin: UberAdmin,
        _language: Language,
    ) -> None:
        """With accept_pe=True, PE submissions map to Y."""
        contest = Contest(
            contest_name="PE Contest",
            contest_url="http://test.example.com",
            login_slug="pe-accept-test",
            start_time=datetime.now(UTC) - timedelta(minutes=30),
            duration_minutes=120,
            stop_answers_after=120,
            stop_updating_scoreboard=100,
            clarifications_timeout_minutes=10,
            created_by_uberadmin_id=uberadmin.id,
            accept_pe=True,
        )
        session.add(contest)
        await session.flush()

        team = await _make_team(session, contest, uberadmin, "t1", "Team 1")
        prob = await _make_problem(session, contest, "Problem A", 1)
        await _make_submission(session, prob, team, _language, 600, Verdict.PE)

        _, zip_bytes = await build_animeitor_zip(session, contest)
        files = _read_zip(zip_bytes)
        runs_line = files["runs"].strip()
        assert runs_line.split(FS)[4] == "Y"

    @pytest.mark.asyncio
    async def test_run_ordering(
        self,
        session: AsyncSession,
        running_contest: Contest,
        uberadmin: UberAdmin,
        _language: Language,
    ) -> None:
        """Runs are ordered by (timestamp_seconds, created_at, id)."""
        team = await _make_team(session, running_contest, uberadmin, "t1", "Team 1")
        prob = await _make_problem(session, running_contest, "Problem A", 1)

        now = datetime.now(UTC)
        # Same timestamp, different created_at
        await _make_submission(
            session,
            prob,
            team,
            _language,
            600,
            Verdict.WA,
            source_code="attempt 1",
            created_at=now - timedelta(seconds=10),
        )
        await _make_submission(
            session,
            prob,
            team,
            _language,
            600,
            Verdict.AC,
            source_code="attempt 2",
            created_at=now - timedelta(seconds=5),
        )
        # Earlier timestamp
        await _make_submission(
            session,
            prob,
            team,
            _language,
            300,
            Verdict.WA,
            source_code="early attempt",
            created_at=now - timedelta(seconds=20),
        )

        _, zip_bytes = await build_animeitor_zip(session, running_contest)
        files = _read_zip(zip_bytes)
        runs_lines = files["runs"].strip().split("\n")

        # Should be: early (300s), first 600s, second 600s
        assert len(runs_lines) == 3
        ids = [int(line.split(FS)[0]) for line in runs_lines]
        assert ids == [1, 2, 3]

        times = [int(line.split(FS)[1]) for line in runs_lines]
        assert times[0] == icpc_minutes_from_seconds(300)
        assert times[1] == icpc_minutes_from_seconds(600)
        assert times[2] == icpc_minutes_from_seconds(600)

        statuses = [line.split(FS)[4] for line in runs_lines]
        assert statuses == ["N", "N", "Y"]
