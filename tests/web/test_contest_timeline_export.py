from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import JudgmentStatus, RoleEnum, TaskType, Verdict
from web.dependencies import ContestAdminContext
from web.models.clarification import Clarification
from web.models.contest import Task
from web.models.language import Language
from web.models.problem import Problem
from web.models.submission import HumanSubmissionConfirmation, Submission, SubmissionJudgment, SubmissionJudgmentAudit
from web.models.users import UberAdmin, User
from web.routes.contest_admin import export_contest_timeline
from web.services.contest_timeline_export_service import build_contest_timeline_report
from web.services.judging_service import override_verdict


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


async def _make_problem(
    session: AsyncSession,
    contest_id: str,
    ordinal: int,
    title: str,
) -> Problem:
    problem = Problem(
        contest_id=contest_id,
        title=title,
        ordinal=ordinal,
        color="#ff0000",
    )
    session.add(problem)
    await session.flush()
    return problem


async def _make_user(
    session: AsyncSession,
    contest_id: str,
    uberadmin: UberAdmin,
    username: str,
    fullname: str,
    role: RoleEnum,
) -> User:
    user = User(
        username=username,
        fullname=fullname,
        role=role,
        contest_id=contest_id,
        created_by_uberadmin_id=uberadmin.id,
    )
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


async def _make_submission(
    session: AsyncSession,
    *,
    contest_start,
    problem: Problem,
    team: User,
    language: Language,
    timestamp_minutes: int,
    autojudge_verdict: Verdict,
) -> tuple[Submission, SubmissionJudgment]:
    created_at = contest_start + timedelta(minutes=timestamp_minutes)
    source_code = f"print({timestamp_minutes})\n"
    submission = Submission(
        problem_id=problem.id,
        team_id=team.id,
        language_id=language.id,
        source_code=source_code,
        source_hash=hashlib.sha256(source_code.encode("utf-8")).hexdigest(),
        source_size_bytes=len(source_code.encode("utf-8")),
        timestamp_seconds=timestamp_minutes * 60,
        created_at=created_at,
    )
    session.add(submission)
    await session.flush()

    judgment = SubmissionJudgment(
        submission_id=submission.id,
        status=JudgmentStatus.DONE,
        autojudge_verdict=autojudge_verdict,
        final_verdict=None,
        created_at=created_at,
        started_at=created_at + timedelta(seconds=15),
        finished_at=created_at + timedelta(minutes=2),
        timestamp_seconds=timestamp_minutes * 60,
    )
    session.add(judgment)
    await session.flush()

    session.add_all(
        [
            SubmissionJudgmentAudit(
                judgment_id=judgment.id,
                submission_id=submission.id,
                actor_user_id=team.id,
                event_source="WEB",
                event_type="SUBMISSION_CREATED",
                from_status=None,
                to_status=JudgmentStatus.QUEUED,
                message="Submission created and enqueued",
                created_at=created_at,
                timestamp_seconds=timestamp_minutes * 60,
            ),
            SubmissionJudgmentAudit(
                judgment_id=judgment.id,
                submission_id=submission.id,
                actor_user_id=None,
                event_source="WORKER",
                event_type="STATUS_CHANGE",
                from_status=JudgmentStatus.QUEUED,
                to_status=JudgmentStatus.DISPATCHED,
                from_verdict=None,
                to_verdict=None,
                message="Worker dispatched judgment",
                created_at=created_at + timedelta(seconds=30),
                timestamp_seconds=(timestamp_minutes * 60) + 30,
            ),
            SubmissionJudgmentAudit(
                judgment_id=judgment.id,
                submission_id=submission.id,
                actor_user_id=None,
                event_source="WORKER",
                event_type="STATUS_CHANGE",
                from_status=JudgmentStatus.DISPATCHED,
                to_status=JudgmentStatus.DONE,
                from_verdict=None,
                to_verdict=autojudge_verdict,
                message="Worker finalized judgment",
                created_at=created_at + timedelta(minutes=2),
                timestamp_seconds=(timestamp_minutes * 60) + 120,
            ),
        ]
    )
    await session.flush()
    return submission, judgment


@pytest.mark.asyncio
async def test_build_contest_timeline_report_renders_wrapped_markdown_table(
    session: AsyncSession,
    running_contest,
    uberadmin: UberAdmin,
    admin_user: User,
    judge_user: User,
    team_user: User,
) -> None:
    running_contest.chief_judge_id = judge_user.id
    language = await _make_language(session)
    staff_user = await _make_user(
        session,
        running_contest.id,
        uberadmin,
        "staff_timeline",
        "Staff Timeline Handler",
        RoleEnum.STAFF,
    )
    problem_a = await _make_problem(
        session,
        running_contest.id,
        1,
        "Uma casinha no meio do nada com um titulo grande o bastante para forcar wrap",
    )
    problem_b = await _make_problem(session, running_contest.id, 2, "Segundo problema")

    submission_a, judgment_a = await _make_submission(
        session,
        contest_start=running_contest.start_time,
        problem=problem_a,
        team=team_user,
        language=language,
        timestamp_minutes=5,
        autojudge_verdict=Verdict.WA,
    )
    submission_b, judgment_b = await _make_submission(
        session,
        contest_start=running_contest.start_time,
        problem=problem_b,
        team=team_user,
        language=language,
        timestamp_minutes=12,
        autojudge_verdict=Verdict.TLE,
    )

    first_confirmation = HumanSubmissionConfirmation(
        judgment=judgment_a,
        judge_id=judge_user.id,
        confirmed_verdict=Verdict.WA,
        is_chief_confirmation=False,
        created_at=running_contest.start_time + timedelta(minutes=8),
        timestamp_seconds=8 * 60,
    )
    session.add(first_confirmation)
    second_judge = await _make_user(
        session,
        running_contest.id,
        uberadmin,
        "judge_timeline_2",
        "Judge Timeline Two",
        RoleEnum.JUDGE,
    )
    second_confirmation = HumanSubmissionConfirmation(
        judgment=judgment_a,
        judge_id=second_judge.id,
        confirmed_verdict=Verdict.WA,
        is_chief_confirmation=False,
        created_at=running_contest.start_time + timedelta(minutes=9),
        timestamp_seconds=9 * 60,
    )
    session.add(second_confirmation)
    chief_confirmation = HumanSubmissionConfirmation(
        judgment=judgment_b,
        judge_id=judge_user.id,
        confirmed_verdict=Verdict.TLE,
        is_chief_confirmation=True,
        created_at=running_contest.start_time + timedelta(minutes=15),
        timestamp_seconds=15 * 60,
    )
    session.add(chief_confirmation)
    await session.flush()

    override = await override_verdict(
        session,
        submission_b.id,
        Verdict.AC,
        "Chief judge corrected the final verdict after checking the evidence.",
        judge_user,
        running_contest,
    )
    override.created_at = running_contest.start_time + timedelta(minutes=16)
    override.timestamp_seconds = 16 * 60
    await session.flush()

    requeue_judgment = SubmissionJudgment(
        submission_id=submission_a.id,
        status=JudgmentStatus.QUEUED,
        created_at=running_contest.start_time + timedelta(minutes=18),
        timestamp_seconds=18 * 60,
    )
    session.add(requeue_judgment)
    await session.flush()

    clarification = Clarification(
        team_id=team_user.id,
        judge_id=judge_user.id,
        problem_id=problem_a.id,
        question="Can we assume coordinates are distinct?",
        answer="Yes.",
        is_contest_public=False,
        created_at=running_contest.start_time + timedelta(minutes=6),
        created_timestamp_seconds=6 * 60,
        answered_at=running_contest.start_time + timedelta(minutes=7),
        answered_timestamp_seconds=7 * 60,
    )
    announcement = Clarification(
        team_id=judge_user.id,
        judge_id=judge_user.id,
        problem_id=problem_a.id,
        question="Announcement",
        answer="Clarified sample formatting.",
        is_contest_public=True,
        created_at=running_contest.start_time + timedelta(minutes=11),
        created_timestamp_seconds=11 * 60,
        answered_at=running_contest.start_time + timedelta(minutes=11),
        answered_timestamp_seconds=11 * 60,
    )
    session.add_all([clarification, announcement])

    balloon_task = Task(
        team_id=team_user.id,
        staff_id=staff_user.id,
        type=TaskType.BALLOON,
        problem_id=problem_b.id,
        source_code="",
        source_hash=hashlib.sha256(b"").hexdigest(),
        source_size_bytes=0,
        created_at=running_contest.start_time + timedelta(minutes=16),
        created_timestamp_seconds=16 * 60,
        finished_at=running_contest.start_time + timedelta(minutes=17),
        finished_timestamp_seconds=17 * 60,
    )
    print_task = Task(
        team_id=team_user.id,
        staff_id=staff_user.id,
        type=TaskType.PRINT,
        problem_id=problem_a.id,
        source_code="print('x')",
        source_hash=hashlib.sha256(b"print('x')").hexdigest(),
        source_size_bytes=10,
        created_at=running_contest.start_time + timedelta(minutes=13),
        created_timestamp_seconds=13 * 60,
        finished_at=running_contest.start_time + timedelta(minutes=14),
        finished_timestamp_seconds=14 * 60,
    )
    sos_task = Task(
        team_id=team_user.id,
        staff_id=staff_user.id,
        type=TaskType.SOS,
        problem_id=None,
        source_code="",
        source_hash=hashlib.sha256(b"").hexdigest(),
        source_size_bytes=0,
        created_at=running_contest.start_time + timedelta(minutes=19),
        created_timestamp_seconds=19 * 60,
        finished_at=running_contest.start_time + timedelta(minutes=20),
        finished_timestamp_seconds=20 * 60,
    )
    session.add_all([balloon_task, print_task, sos_task])
    await session.commit()

    filename, content = await build_contest_timeline_report(session, running_contest)

    assert filename == f"contest-timeline-{running_contest.login_slug}.md"
    assert "Contest starts" in content
    assert "Team submits code for problem A" in content
    assert "Autojudge starts" in content
    assert "Autojudge ends" in content
    assert "Judge publishes an announcement" in content
    assert "Team asks for a clarification" in content
    assert "Judge answers a clarification" in content
    assert "Team issues a print task" in content
    assert "Staff handles printout to a team" in content
    assert "Team issues a SOS task" in content
    assert "Staff answers a SOS task" in content
    assert "Balloon task is issued" in content
    assert "Team gets a balloon" in content
    assert "Submission is requeued for autojudge" in content
    assert "Scoreboard stops updating" in content
    assert "Answers stop being issued" in content
    assert "Contest ends" in content
    assert "```text" in content


@pytest.mark.asyncio
async def test_export_contest_timeline_route_returns_markdown_attachment(
    session: AsyncSession,
    running_contest,
    admin_user: User,
) -> None:
    ctx = ContestAdminContext(contest=running_contest, session=session, actor=admin_user)

    response = await export_contest_timeline(ctx=ctx)

    assert response.status_code == 200
    assert response.media_type == "text/markdown; charset=utf-8"
    assert response.headers["Content-Disposition"] == 'attachment; filename="contest-timeline-test-contest.md"'
