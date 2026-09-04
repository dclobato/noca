#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the lean, report-only submission query."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import submission_judgments as submission_judgments_table
from shared.enumerations import JudgmentStatus, ProblemValidatorType, RoleEnum, Verdict
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem
from web.models.site import Site
from web.models.submission import Submission, SubmissionJudgment
from web.models.users import UberAdmin, User
from web.services.contest_report_query_service import list_contest_report_problems, list_contest_report_submissions
from web.services.contest_report_service import compute_contest_report
from web.services.contest_user_service import count_teams_by_site

_BASE_TIME = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


async def _make_language(session: AsyncSession) -> Language:
    language = Language(
        id="report-query-python",
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


async def _make_problem(session: AsyncSession, contest: Contest, *, ordinal: int, title: str, color: str) -> Problem:
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


async def _make_team(
    session: AsyncSession,
    contest: Contest,
    uberadmin: UberAdmin,
    *,
    username: str,
    fullname: str,
    site: Site | None = None,
) -> User:
    user = User(
        username=username,
        fullname=fullname,
        role=RoleEnum.TEAM,
        contest_id=contest.id,
        created_by_uberadmin_id=uberadmin.id,
        site_id=site.id if site else None,
    )
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


async def _add_submission(
    session: AsyncSession,
    *,
    problem: Problem,
    team: User,
    language: Language,
    seconds: int,
) -> Submission:
    source = f"print('{problem.id}-{team.id}-{seconds}')\n"
    created_at = _BASE_TIME + timedelta(seconds=seconds)
    submission = Submission(
        problem_id=problem.id,
        team_id=team.id,
        language_id=language.id,
        source_code=source,
        source_hash=hashlib.sha256(f"{source}{seconds}".encode()).hexdigest(),
        source_size_bytes=len(source.encode()),
        timestamp_seconds=seconds,
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(submission)
    await session.flush()
    return submission


async def _add_judgment(
    session: AsyncSession,
    submission: Submission,
    *,
    status: JudgmentStatus,
    verdict: Verdict | None,
    offset_seconds: int,
) -> SubmissionJudgment:
    created_at = _BASE_TIME + timedelta(seconds=submission.timestamp_seconds + offset_seconds)
    judgment = SubmissionJudgment(
        submission_id=submission.id,
        status=status,
        autojudge_verdict=verdict if status == JudgmentStatus.DONE else None,
        created_at=created_at,
    )
    session.add(judgment)
    await session.flush()
    # `final_verdict` is re-derived from human confirmations by a before_flush
    # hook (web/models/submission.py) whenever the judgment is touched, which
    # would otherwise clobber the constructor's value back to None -- a raw
    # Core UPDATE bypasses the ORM session so it sticks, matching the pattern
    # `test_contest_dashboard_solved_problems._make_judged_submission` uses.
    if verdict is not None:
        await session.execute(
            update(submission_judgments_table)
            .where(submission_judgments_table.c.id == judgment.id)
            .values(final_verdict=verdict.value)
        )
        await session.flush()
    return judgment


@pytest.mark.asyncio
async def test_list_contest_report_submissions_flattens_fields_and_resolves_effective_judgment(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """Each row carries flattened problem/team fields and the same effective
    judgment `judgment_utils.get_active_judgment` would pick -- excluding
    SUPERSEDED and FAILED, preferring the latest `created_at` among the rest.
    """
    language = await _make_language(session)
    problem_a = await _make_problem(session, running_contest, ordinal=1, title="Arrays", color="#dd2233")
    problem_b = await _make_problem(session, running_contest, ordinal=2, title="Bridges", color="#00aaee")

    site = Site(sitename="Site One", sitename_normalized="site one", contest_id=running_contest.id)
    session.add(site)
    await session.flush()

    team_with_site = await _make_team(
        session, running_contest, uberadmin, username="team_site", fullname="Team Site", site=site
    )
    team_reattempted = await _make_team(session, running_contest, uberadmin, username="team_re", fullname="Team Re")
    team_failed_only = await _make_team(
        session, running_contest, uberadmin, username="team_failed", fullname="Team Failed"
    )

    # Submission 1: a single DONE/AC judgment.
    submission_1 = await _add_submission(session, problem=problem_a, team=team_with_site, language=language, seconds=10)
    await _add_judgment(session, submission_1, status=JudgmentStatus.DONE, verdict=Verdict.AC, offset_seconds=1)

    # Submission 2: an earlier SUPERSEDED WA judgment must be ignored in favor
    # of the later DONE/AC judgment -- a rejudge overwriting a stale verdict.
    submission_2 = await _add_submission(
        session, problem=problem_a, team=team_reattempted, language=language, seconds=20
    )
    await _add_judgment(session, submission_2, status=JudgmentStatus.SUPERSEDED, verdict=Verdict.WA, offset_seconds=1)
    await _add_judgment(session, submission_2, status=JudgmentStatus.DONE, verdict=Verdict.AC, offset_seconds=2)

    # Submission 3: only a FAILED judgment -- no effective judgment at all.
    submission_3 = await _add_submission(
        session, problem=problem_b, team=team_failed_only, language=language, seconds=30
    )
    await _add_judgment(session, submission_3, status=JudgmentStatus.FAILED, verdict=None, offset_seconds=1)

    rows = await list_contest_report_submissions(session, running_contest)
    rows_by_id = {row.id: row for row in rows}

    assert set(rows_by_id) == {submission_1.id, submission_2.id, submission_3.id}

    row_1 = rows_by_id[submission_1.id]
    assert row_1.problem_id == problem_a.id
    assert row_1.problem_ordinal == 1
    assert row_1.problem_title == "Arrays"
    assert row_1.problem_color == "#dd2233"
    assert row_1.team_id == team_with_site.id
    assert row_1.team_username == "team_site"
    assert row_1.team_fullname == "Team Site"
    assert row_1.team_site_name == "Site One"
    assert row_1.language_id == language.id
    assert row_1.timestamp_seconds == 10
    assert row_1.judgment_status == JudgmentStatus.DONE
    assert row_1.final_verdict == Verdict.AC

    row_2 = rows_by_id[submission_2.id]
    assert row_2.judgment_status == JudgmentStatus.DONE
    assert row_2.final_verdict == Verdict.AC  # not the superseded WA
    assert row_2.team_site_name is None

    row_3 = rows_by_id[submission_3.id]
    assert row_3.judgment_status is None
    assert row_3.final_verdict is None


@pytest.mark.asyncio
async def test_list_contest_report_submissions_feeds_compute_contest_report(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """The query's output is consumable end-to-end by `compute_contest_report`,
    including counting a team with no judged runs as still "active", and a
    team with no submissions at all as enrolled-but-not-active.
    """
    language = await _make_language(session)
    problem = await _make_problem(session, running_contest, ordinal=1, title="Arrays", color="#dd2233")
    # No team ever submits to this problem -- it must still appear in the
    # report, with honest zeros, instead of vanishing from every table.
    untouched_problem = await _make_problem(session, running_contest, ordinal=2, title="Bridges", color="#00aaee")

    team_a = await _make_team(session, running_contest, uberadmin, username="team_a", fullname="Team A")
    team_b = await _make_team(session, running_contest, uberadmin, username="team_b", fullname="Team B")
    team_c = await _make_team(session, running_contest, uberadmin, username="team_c", fullname="Team C")
    # team_d is enrolled but never submits anything -- not "active".
    await _make_team(session, running_contest, uberadmin, username="team_d", fullname="Team D")

    # team_a gets it wrong once before solving -- contributes to dirt.
    submission_a_wa = await _add_submission(session, problem=problem, team=team_a, language=language, seconds=5)
    await _add_judgment(session, submission_a_wa, status=JudgmentStatus.DONE, verdict=Verdict.WA, offset_seconds=1)
    submission_a = await _add_submission(session, problem=problem, team=team_a, language=language, seconds=10)
    await _add_judgment(session, submission_a, status=JudgmentStatus.DONE, verdict=Verdict.AC, offset_seconds=1)

    submission_b = await _add_submission(session, problem=problem, team=team_b, language=language, seconds=20)
    await _add_judgment(session, submission_b, status=JudgmentStatus.DONE, verdict=Verdict.AC, offset_seconds=1)
    # team_b resubmits AC again (e.g. a cleaner rewrite) -- must not inflate
    # the solved-by distribution beyond "1 team solved it".
    submission_b2 = await _add_submission(session, problem=problem, team=team_b, language=language, seconds=25)
    await _add_judgment(session, submission_b2, status=JudgmentStatus.DONE, verdict=Verdict.AC, offset_seconds=1)

    # team_c submitted but has no judged run at all -- still "active".
    submission_c = await _add_submission(session, problem=problem, team=team_c, language=language, seconds=30)
    await _add_judgment(session, submission_c, status=JudgmentStatus.FAILED, verdict=None, offset_seconds=1)

    rows = await list_contest_report_submissions(session, running_contest)
    problems = await list_contest_report_problems(session, running_contest)
    report = compute_contest_report(running_contest, rows, problems, [language], 4)

    assert report.total_runs == 4
    assert report.total_accepted == 3
    assert [p.label for p in report.highlights.most_solved.problems] == ["A"]
    assert report.highlights.most_solved.solved_teams == 2
    assert report.highlights.most_solved.pct_of_teams == pytest.approx(round(2 / 3 * 100, 2))
    # Bridges (problem B) has zero submissions from anyone -- it must still
    # appear, on the unsolved card rather than as the least-solved problem,
    # rather than being absent from the problem set entirely. "Least solved"
    # ranges over problems somebody actually solved, so here it is A, the
    # only one with any accepted run at all.
    assert [p.label for p in report.highlights.unsolved] == ["B"]
    assert report.highlights.least_solved is not None
    assert [p.label for p in report.highlights.least_solved.problems] == ["A"]
    assert report.highlights.least_solved.solved_teams == 2
    assert report.highlights.active_teams.active == 3
    assert report.highlights.active_teams.enrolled == 4
    assert report.highlights.active_teams.pct == pytest.approx(75.0)

    assert [p.label for p in report.problems] == ["A", "B"]
    assert report.problems[1].title == untouched_problem.title
    assert report.problem_summary[1].total_runs == 0
    assert report.problem_summary[1].ac.count == 0
    assert report.problem_summary[1].solve_metrics.dirt_ratio is None
    assert report.problem_summary[1].solve_metrics.first_solver_name is None
    assert report.accepted_distribution[1].count == 0
    assert report.problem_race[1].problem.label == "B"
    assert report.problem_race[1].solved_minutes == []

    # 3 accepted submissions but only 2 distinct solving teams -- team_b's
    # second AC on the same problem must not inflate the solved-by count.
    assert report.accepted_distribution[0].count == 2

    # Problem Race: one solve-minute entry per solving team (team_a at
    # second 10 -> minute 0, team_b at second 20 -> minute 0; team_b's
    # second AC at second 25 does not add a second entry).
    assert report.problem_race[0].problem.label == "A"
    assert report.problem_race[0].solved_minutes == [0, 0]
    assert report.accepted_distribution[0].pct == pytest.approx(100.0)

    # team_a solved at second 10, before team_b's second-20 solve.
    assert report.problem_summary[0].solve_metrics.first_solver_name == "Team A"
    # team_a's WA before its AC is the only wrong attempt among the 2
    # solvers: dirt = 1 wrong / (1 wrong + 2 solvers) = 33.33%.
    assert report.problem_summary[0].solve_metrics.dirt_ratio == pytest.approx(round(1 / 3 * 100, 2))

    # Performance: team_a solved 1 (with a 20-minute WA penalty, the
    # contest's default wa_penalty), team_b solved 1 (no penalty, no wrong
    # attempts before its AC), team_c solved 0 -- still counted as active.
    performance = report.performance
    assert performance.active_team_count == 3
    assert performance.solved_summary is not None
    assert performance.solved_summary.minimum == 0
    assert performance.solved_summary.maximum == 1
    assert performance.solved_summary.mean == pytest.approx(2 / 3)
    assert performance.solved_summary.median == pytest.approx(1.0)
    assert performance.penalty_summary is not None
    assert performance.penalty_summary.mean == pytest.approx(20 / 3)
    assert performance.penalty_summary.median == pytest.approx(0.0)
    assert performance.top_10pct_solved == 1
    histogram_by_solved = {bucket.solved: bucket.team_count for bucket in performance.solved_histogram}
    assert histogram_by_solved == {0: 1, 1: 2}


@pytest.mark.asyncio
async def test_list_contest_report_submissions_filters_by_site(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """`site_id` scopes the query to one site's teams; a team with no site is
    excluded from any site-scoped query, and `count_teams_by_site` reports
    the same per-site team counts the picker grid relies on.
    """
    language = await _make_language(session)
    problem = await _make_problem(session, running_contest, ordinal=1, title="Arrays", color="#dd2233")

    site_a = Site(sitename="Site A", sitename_normalized="site a", contest_id=running_contest.id)
    site_b = Site(sitename="Site B", sitename_normalized="site b", contest_id=running_contest.id)
    session.add_all([site_a, site_b])
    await session.flush()

    team_a1 = await _make_team(session, running_contest, uberadmin, username="team_a1", fullname="A1", site=site_a)
    team_a2 = await _make_team(session, running_contest, uberadmin, username="team_a2", fullname="A2", site=site_a)
    team_b1 = await _make_team(session, running_contest, uberadmin, username="team_b1", fullname="B1", site=site_b)
    # No site at all -- must be excluded from every site-scoped query.
    team_no_site = await _make_team(session, running_contest, uberadmin, username="team_x", fullname="X")

    submission_a1 = await _add_submission(session, problem=problem, team=team_a1, language=language, seconds=10)
    await _add_judgment(session, submission_a1, status=JudgmentStatus.DONE, verdict=Verdict.AC, offset_seconds=1)
    submission_a2 = await _add_submission(session, problem=problem, team=team_a2, language=language, seconds=20)
    await _add_judgment(session, submission_a2, status=JudgmentStatus.DONE, verdict=Verdict.AC, offset_seconds=1)
    submission_b1 = await _add_submission(session, problem=problem, team=team_b1, language=language, seconds=30)
    await _add_judgment(session, submission_b1, status=JudgmentStatus.DONE, verdict=Verdict.AC, offset_seconds=1)
    submission_no_site = await _add_submission(
        session, problem=problem, team=team_no_site, language=language, seconds=40
    )
    await _add_judgment(session, submission_no_site, status=JudgmentStatus.DONE, verdict=Verdict.AC, offset_seconds=1)

    unscoped = await list_contest_report_submissions(session, running_contest)
    assert {row.id for row in unscoped} == {
        submission_a1.id,
        submission_a2.id,
        submission_b1.id,
        submission_no_site.id,
    }

    scoped_to_a = await list_contest_report_submissions(session, running_contest, site_id=site_a.id)
    assert {row.id for row in scoped_to_a} == {submission_a1.id, submission_a2.id}

    scoped_to_b = await list_contest_report_submissions(session, running_contest, site_id=site_b.id)
    assert {row.id for row in scoped_to_b} == {submission_b1.id}

    team_counts = await count_teams_by_site(session, running_contest)
    assert team_counts == {site_a.id: 2, site_b.id: 1}
