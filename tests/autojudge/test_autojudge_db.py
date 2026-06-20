"""
Tests for autojudge.db — the worker's database access layer.

Uses the existing conftest.py ``engine`` fixture (in-memory SQLite with full
web ORM schema).  The autojudge Core tables map onto the same physical tables
created by ``Base.metadata.create_all``, so Core queries work directly.

Seed data is inserted via the web ORM (``session``), then queried through
``autojudge.db.DatabaseAccess`` to verify the worker sees the correct values.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from arena.models.arena_notifications import ArenaNotification
from arena.models.arena_problems import ArenaProblem, ArenaRatingProblem, ArenaTestCase
from arena.models.arena_submissions import (
    ArenaSubmission,
    ArenaSubmissionJudgment,
    ArenaSubmissionTestResult,
    ArenaUserSolvedProblem,
)
from arena.models.arena_users import ArenaUser
from autojudge.config import settings as autojudge_settings
from autojudge.db import ProfilingObservedLimits, open_db
from shared.enumerations import ArenaRole, JudgmentStatus, ProfilingStatus, TaskType, Verdict
from shared.services.testcase_files import save_testcase_files
from web.models.contest import Contest, Task
from web.models.language import Language
from web.models.problem import Problem, ProblemLanguageLimit, ProblemTestCase, ProfilingRun
from web.models.submission import (
    Submission,
    SubmissionJudgment,
    SubmissionJudgmentAudit,
    SubmissionTestResult,
)
from web.models.users import User

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _uid() -> str:
    return str(uuid.uuid4())


def _add_arena_tc(
    session: AsyncSession,
    problem_id: str,
    ordinal: int,
    input_content: str = "1\n",
    output_content: str = "1\n",
) -> ArenaTestCase:
    """Write Arena test-case files to the autojudge arena root and add a row.

    Content lives on disk (``<root>/arena/<problem_id>/NNN.in|out``); the row
    stores only metadata and the normalized on-disk byte sizes.
    """
    in_size, out_size = save_testcase_files(
        problem_id,
        ordinal,
        input_content.encode("utf-8"),
        output_content.encode("utf-8"),
        autojudge_settings.arena_testcase_dir,
    )
    tc = ArenaTestCase(
        problem_id=problem_id,
        ordinal=ordinal,
        input_size_bytes=in_size,
        output_size_bytes=out_size,
    )
    session.add(tc)
    return tc


def _make_language(session: AsyncSession, lang_id: str = "python3") -> Language:
    lang = Language(
        id=lang_id,
        name="Python 3.14",
        icon="python",
        compile_image="noca/judge-python3:compile",
        run_image="noca/judge-python3:run",
        compile_cmd=["python3", "-m", "py_compile", "/sandbox/source.py"],
        run_cmd=["python3", "-u", "/sandbox/source.py"],
        source_filename="source.py",
        artifact_path="/sandbox/source.py",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(lang)
    return lang


def _make_inactive_language(session: AsyncSession) -> Language:
    lang = Language(
        id="disabled-lang",
        name="Disabled",
        icon="",
        compile_image="img:compile",
        run_image="img:run",
        compile_cmd=None,
        run_cmd=["run"],
        source_filename="src",
        artifact_path="/sandbox/src",
        artifact_is_source=True,
        compile_timeout_s=5.0,
        active=False,
    )
    session.add(lang)
    return lang


def _make_arena_user(session: AsyncSession, *, role: ArenaRole = ArenaRole.ARENA_USER) -> ArenaUser:
    user = ArenaUser(
        nome="Arena Judge User",
        email_normalizado=f"arena-{uuid.uuid4().hex[:8]}@test.example.com",
        dta_nascimento=None,
        role=role,
    )
    user.password = "Senha@Forte1!"
    session.add(user)
    return user


def _make_submission(
    session: AsyncSession,
    problem: Problem,
    team: User,
    language: Language,
    source: str = "print('hello')",
) -> Submission:
    src_hash = hashlib.sha256(source.encode()).hexdigest()
    sub = Submission(
        problem_id=problem.id,
        team_id=team.id,
        language_id=language.id,
        source_code=source,
        source_hash=src_hash,
        source_size_bytes=len(source.encode()),
    )
    session.add(sub)
    return sub


def _make_judgment(
    session: AsyncSession,
    submission: Submission,
    status: JudgmentStatus = JudgmentStatus.QUEUED,
) -> SubmissionJudgment:
    j = SubmissionJudgment(
        submission_id=submission.id,
        status=status,
    )
    session.add(j)
    return j


# ---------------------------------------------------------------------------
# Tests — get_submission_for_judging
# ---------------------------------------------------------------------------


async def test_get_submission_for_judging_happy_path(
    engine,
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
    team_user: User,
):
    lang = _make_language(session)
    await session.flush()
    sub = _make_submission(session, contest_problem, team_user, lang)
    await session.flush()
    j = _make_judgment(session, sub)
    await session.flush()
    await session.commit()

    async with open_db(engine) as db:
        qs = await db.get_submission_for_judging(j.id)

    assert qs.judgment_id == j.id
    assert qs.submission_id == sub.id
    assert qs.contest_id == running_contest.id
    assert qs.problem_id == contest_problem.id
    assert qs.team_id == team_user.id
    assert qs.language_id == lang.id
    assert qs.source_code == "print('hello')"
    assert qs.autojudge_only is True  # Contest default


async def test_get_submission_for_judging_done_raises(
    engine,
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
    team_user: User,
):
    lang = _make_language(session)
    await session.flush()
    sub = _make_submission(session, contest_problem, team_user, lang)
    await session.flush()
    j = _make_judgment(session, sub, status=JudgmentStatus.DONE)
    j.autojudge_verdict = Verdict.AC
    await session.flush()
    await session.commit()

    async with open_db(engine) as db:
        with pytest.raises(LookupError, match="not judgeable"):
            await db.get_submission_for_judging(j.id)


async def test_get_submission_for_judging_missing_raises(engine, session: AsyncSession):
    async with open_db(engine) as db:
        with pytest.raises(LookupError, match="not found"):
            await db.get_submission_for_judging(_uid())


async def test_get_arena_submission_for_judging_happy_path(engine, session: AsyncSession) -> None:
    """Worker DB access should load Arena submission payload and DB-backed test cases."""
    lang = _make_language(session, lang_id=f"arena-lang-{uuid.uuid4().hex[:6]}")
    user = _make_arena_user(session)
    await session.flush()
    problem = ArenaProblem(
        arena_number=1,
        title="Arena DB Problem",
        owner_id=user.id,
        problem_statement="<p>Echo.</p>",
        time_limit_ms=1500,
        memory_limit_kb=65536,
        pids_limit=32,
        output_limit_in_bytes=4096,
    )
    session.add(problem)
    await session.flush()
    _add_arena_tc(session, problem.id, 1)
    submission = ArenaSubmission(
        user_id=user.id,
        problem_id=problem.id,
        language_id=lang.id,
        source_code="print(input())",
        source_hash="b" * 64,
        source_size_bytes=14,
    )
    session.add(submission)
    await session.flush()
    judgment = ArenaSubmissionJudgment(submission_id=submission.id, status=JudgmentStatus.QUEUED.value)
    session.add(judgment)
    await session.commit()

    async with open_db(engine) as db:
        queued = await db.get_arena_submission_for_judging(judgment.id)

    assert queued.judgment_id == judgment.id
    assert queued.submission_id == submission.id
    assert queued.user_id == user.id
    assert queued.problem_id == problem.id
    assert queued.problem_number == problem.arena_number
    assert queued.problem_title == problem.title
    assert queued.language_id == lang.id
    assert queued.limits.time_limit_ms == 1500
    assert queued.test_cases[0].input_data == b"1\n"


async def test_get_arena_submission_for_judging_normalizes_legacy_line_endings(
    engine,
    session: AsyncSession,
) -> None:
    """Worker payloads should normalize legacy CRLF and lone-CR database content."""
    lang = _make_language(session, lang_id=f"arena-normalize-{uuid.uuid4().hex[:6]}")
    user = _make_arena_user(session)
    await session.flush()
    problem = ArenaProblem(
        arena_number=2,
        title="Arena Legacy Line Endings",
        owner_id=user.id,
        problem_statement="<p>Echo.</p>",
    )
    session.add(problem)
    await session.flush()
    _add_arena_tc(
        session,
        problem.id,
        1,
        input_content="first\r\n\r\nsecond\rthird\r\n",
        output_content="YES\r\nMAYBE\rNO\r\n",
    )
    submission = ArenaSubmission(
        user_id=user.id,
        problem_id=problem.id,
        language_id=lang.id,
        source_code="print(input())",
        source_hash="f" * 64,
        source_size_bytes=14,
    )
    session.add(submission)
    await session.flush()
    judgment = ArenaSubmissionJudgment(
        submission_id=submission.id,
        status=JudgmentStatus.QUEUED.value,
    )
    session.add(judgment)
    await session.commit()

    async with open_db(engine) as db:
        queued = await db.get_arena_submission_for_judging(judgment.id)

    assert queued.test_cases[0].input_data == b"first\n\nsecond\nthird\n"
    assert queued.test_cases[0].expected_output == b"YES\nMAYBE\nNO\n"


async def test_arena_judgment_dispatched_clears_stale_result(engine, session: AsyncSession) -> None:
    """Arena retry dispatch must remove the previous first-failure row."""
    lang = _make_language(session, lang_id=f"arena-lang-{uuid.uuid4().hex[:6]}")
    user = _make_arena_user(session)
    await session.flush()
    problem = ArenaProblem(
        arena_number=1,
        title="Arena Retry Problem",
        owner_id=user.id,
        problem_statement="<p>Echo.</p>",
    )
    session.add(problem)
    await session.flush()
    test_case = _add_arena_tc(session, problem.id, 1)
    session.add(test_case)
    submission = ArenaSubmission(
        user_id=user.id,
        problem_id=problem.id,
        language_id=lang.id,
        source_code="bad",
        source_hash="c" * 64,
        source_size_bytes=3,
    )
    session.add(submission)
    await session.flush()
    judgment = ArenaSubmissionJudgment(
        submission_id=submission.id,
        status=JudgmentStatus.JUDGING.value,
        autojudge_verdict=Verdict.WA.value,
        final_verdict=Verdict.WA.value,
        compile_log="old",
    )
    session.add(judgment)
    await session.flush()
    session.add(
        ArenaSubmissionTestResult(
            judgment_id=judgment.id,
            test_case_id=test_case.id,
            verdict=Verdict.WA.value,
        )
    )
    await session.commit()

    async with open_db(engine) as db:
        await db.set_arena_judgment_dispatched(judgment.id, "arena-worker")

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        reloaded = await vs.get(ArenaSubmissionJudgment, judgment.id)
        assert reloaded is not None
        assert reloaded.status == JudgmentStatus.DISPATCHED.value
        assert reloaded.worker_id == "arena-worker"
        assert reloaded.autojudge_verdict is None
        rows = (await vs.execute(select(ArenaSubmissionTestResult))).scalars().all()
        assert rows == []


async def test_arena_judgment_done_records_first_solver_stats(engine, session: AsyncSession) -> None:
    """First Arena AC should create solver row and increment solved counters once."""
    lang = _make_language(session, lang_id=f"arena-lang-{uuid.uuid4().hex[:6]}")
    author = _make_arena_user(session)
    user = _make_arena_user(session)
    await session.flush()
    problem = ArenaProblem(
        arena_number=1,
        title="Arena Solve Problem",
        owner_id=author.id,
        problem_statement="<p>Echo.</p>",
    )
    session.add(problem)
    await session.flush()
    session.add(ArenaRatingProblem(problem_id=problem.id, attempted_users=1, total_submissions=2))
    _add_arena_tc(session, problem.id, 1)
    first_submission = ArenaSubmission(
        user_id=user.id,
        problem_id=problem.id,
        language_id=lang.id,
        source_code="bad",
        source_hash="d" * 64,
        source_size_bytes=3,
    )
    accepted_submission = ArenaSubmission(
        user_id=user.id,
        problem_id=problem.id,
        language_id=lang.id,
        source_code="good",
        source_hash="e" * 64,
        source_size_bytes=4,
    )
    session.add_all([first_submission, accepted_submission])
    await session.flush()
    judgment = ArenaSubmissionJudgment(submission_id=accepted_submission.id, status=JudgmentStatus.JUDGING.value)
    session.add(judgment)
    await session.commit()

    async with open_db(engine) as db:
        queued = await db.get_arena_submission_for_judging(judgment.id)
        await db.set_arena_judgment_done(queued, Verdict.AC, max_wall_time_ms=12, max_memory_kb=1024)
        await db.set_arena_judgment_done(queued, Verdict.AC, max_wall_time_ms=12, max_memory_kb=1024)

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        solver = (
            await vs.execute(
                select(ArenaUserSolvedProblem).where(
                    ArenaUserSolvedProblem.problem_id == problem.id,
                    ArenaUserSolvedProblem.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        assert solver is not None
        rating = await vs.get(ArenaRatingProblem, problem.id)
        assert rating is not None
        assert rating.solved_users == 1
        assert rating.total_tries_before_solve == 2
        notifications = (
            (
                await vs.execute(
                    select(ArenaNotification).where(
                        ArenaNotification.user_id == user.id,
                        ArenaNotification.source_ref == judgment.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(notifications) == 1
        notification = notifications[0]
        assert notification.notification_kind == "SUBMISSION_JUDGED"
        assert notification.target_url == f"/submissions/{accepted_submission.id}"
        assert notification.message == (
            "Your submission for problem 1 - Arena Solve Problem was judged. View the result."
        )


async def test_arena_judgment_done_records_admin_solve_without_rating_counters(
    engine,
    session: AsyncSession,
) -> None:
    """An admin first AC is personal progress, not aggregate rating evidence."""
    lang = _make_language(session, lang_id=f"arena-lang-{uuid.uuid4().hex[:6]}")
    author = _make_arena_user(session)
    admin = _make_arena_user(session, role=ArenaRole.ARENA_ADMIN)
    await session.flush()
    problem = ArenaProblem(
        arena_number=1,
        title="Arena Staff Solve Problem",
        owner_id=author.id,
        problem_statement="<p>Echo.</p>",
    )
    session.add(problem)
    await session.flush()
    session.add(ArenaRatingProblem(problem_id=problem.id, attempted_users=0, total_submissions=1))
    _add_arena_tc(session, problem.id, 1)
    accepted_submission = ArenaSubmission(
        user_id=admin.id,
        problem_id=problem.id,
        language_id=lang.id,
        source_code="good",
        source_hash="e" * 64,
        source_size_bytes=4,
    )
    session.add(accepted_submission)
    await session.flush()
    judgment = ArenaSubmissionJudgment(submission_id=accepted_submission.id, status=JudgmentStatus.JUDGING.value)
    session.add(judgment)
    await session.commit()

    async with open_db(engine) as db:
        queued = await db.get_arena_submission_for_judging(judgment.id)
        await db.set_arena_judgment_done(queued, Verdict.AC, max_wall_time_ms=12, max_memory_kb=1024)

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        solver = (
            await vs.execute(
                select(ArenaUserSolvedProblem).where(
                    ArenaUserSolvedProblem.problem_id == problem.id,
                    ArenaUserSolvedProblem.user_id == admin.id,
                )
            )
        ).scalar_one_or_none()
        assert solver is not None
        rating = await vs.get(ArenaRatingProblem, problem.id)
        assert rating is not None
        assert rating.solved_users == 0
        assert rating.total_tries_before_solve == 0


async def test_arena_judgment_done_excludes_author_self_solve_from_counters(
    engine,
    session: AsyncSession,
) -> None:
    """The author solving their own problem records personal progress only."""
    lang = _make_language(session, lang_id=f"arena-lang-{uuid.uuid4().hex[:6]}")
    author = _make_arena_user(session)
    await session.flush()
    problem = ArenaProblem(
        arena_number=1,
        title="Arena Author Solve Problem",
        owner_id=author.id,
        problem_statement="<p>Echo.</p>",
    )
    session.add(problem)
    await session.flush()
    session.add(ArenaRatingProblem(problem_id=problem.id, attempted_users=0, total_submissions=1))
    _add_arena_tc(session, problem.id, 1)
    accepted_submission = ArenaSubmission(
        user_id=author.id,
        problem_id=problem.id,
        language_id=lang.id,
        source_code="good",
        source_hash="e" * 64,
        source_size_bytes=4,
    )
    session.add(accepted_submission)
    await session.flush()
    judgment = ArenaSubmissionJudgment(submission_id=accepted_submission.id, status=JudgmentStatus.JUDGING.value)
    session.add(judgment)
    await session.commit()

    async with open_db(engine) as db:
        queued = await db.get_arena_submission_for_judging(judgment.id)
        await db.set_arena_judgment_done(queued, Verdict.AC, max_wall_time_ms=12, max_memory_kb=1024)

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        solver = (
            await vs.execute(
                select(ArenaUserSolvedProblem).where(
                    ArenaUserSolvedProblem.problem_id == problem.id,
                    ArenaUserSolvedProblem.user_id == author.id,
                )
            )
        ).scalar_one_or_none()
        assert solver is not None  # personal solved marker still recorded
        rating = await vs.get(ArenaRatingProblem, problem.id)
        assert rating is not None
        assert rating.solved_users == 0
        assert rating.total_tries_before_solve == 0


async def test_set_judgment_dispatched_resets_partial_state(
    engine,
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
    team_user: User,
):
    lang = _make_language(session)
    await session.flush()
    sub = _make_submission(session, contest_problem, team_user, lang)
    await session.flush()
    j = _make_judgment(session, sub, status=JudgmentStatus.JUDGING)
    j.compile_log = "old compile log"
    j.max_wall_time_ms = 123
    j.max_memory_kb = 456
    j.min_wall_time_ms = 12
    j.min_memory_kb = 34
    j.error_message = "old error"
    j.finished_at = running_contest.start_time
    await session.flush()

    tc = ProblemTestCase(problem_id=contest_problem.id, ordinal=1)
    session.add(tc)
    await session.flush()
    session.add(
        SubmissionTestResult(
            judgment_id=j.id,
            test_case_id=tc.id,
            verdict=Verdict.WA,
            wall_time_ms=99,
            memory_kb=88,
            exit_code=1,
            stdout_excerpt="stale",
            stderr_excerpt="stale",
        )
    )
    await session.commit()

    async with open_db(engine) as db:
        await db.set_judgment_dispatched(j.id, "worker-retry", contest_start_time=running_contest.start_time)

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        j_reloaded = await vs.get(SubmissionJudgment, j.id)
        assert j_reloaded is not None
        assert j_reloaded.status == JudgmentStatus.DISPATCHED
        assert j_reloaded.worker_id == "worker-retry"
        assert j_reloaded.compile_log is None
        assert j_reloaded.max_wall_time_ms is None
        assert j_reloaded.max_memory_kb is None
        assert j_reloaded.min_wall_time_ms is None
        assert j_reloaded.min_memory_kb is None
        assert j_reloaded.error_message is None
        assert j_reloaded.finished_at is None

        results = (
            await vs.execute(SubmissionTestResult.__table__.select().where(SubmissionTestResult.judgment_id == j.id))
        ).all()
        assert results == []


# ---------------------------------------------------------------------------
# Tests — get_problem_limits
# ---------------------------------------------------------------------------


async def test_get_problem_limits_base(
    engine,
    session: AsyncSession,
    contest_problem: Problem,
):
    await session.commit()

    async with open_db(engine) as db:
        limits = await db.get_problem_limits(contest_problem.id, "python3")

    assert limits.time_limit_ms == 1000  # default
    assert limits.memory_limit_kb == 262144  # default
    assert limits.pids_limit == 64  # default
    assert limits.repetitions == 1


async def test_get_problem_limits_with_language_override(
    engine,
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
):
    lang = _make_language(session)
    await session.flush()
    override = ProblemLanguageLimit(
        problem_id=contest_problem.id,
        language_id=lang.id,
        time_limit_ms=5000,
        memory_limit_kb=512000,
        pids_limit=128,
        repetitions=7,
    )
    session.add(override)
    await session.flush()
    await session.commit()

    async with open_db(engine) as db:
        limits = await db.get_problem_limits(contest_problem.id, lang.id)

    assert limits.time_limit_ms == 5000
    assert limits.memory_limit_kb == 512000
    assert limits.pids_limit == 128
    assert limits.repetitions == 7


async def test_get_problem_limits_missing_raises(engine, session: AsyncSession):
    async with open_db(engine) as db:
        with pytest.raises(LookupError, match="not found"):
            await db.get_problem_limits(_uid(), "python3")


async def test_set_profiling_done_persists_repetitions(
    engine,
    session: AsyncSession,
    contest_problem: Problem,
):
    lang = _make_language(session, "cpp")
    profiling_run = ProfilingRun(
        problem_id=contest_problem.id,
        language_id=lang.id,
        source_code="int main() { return 0; }",
        source_hash=hashlib.sha256(b"int main() { return 0; }").hexdigest(),
        status=ProfilingStatus.RUNNING,
        safety_factor=1.5,
    )
    session.add_all([lang, profiling_run])
    await session.flush()
    await session.commit()

    async with open_db(engine) as db:
        await db.set_profiling_done(
            profiling_run.id,
            ProfilingObservedLimits(
                time_limit_ms=150,
                memory_limit_kb=4096,
                pids_limit=32,
                output_limit_in_bytes=1024,
            ),
            10,
        )

    refreshed_limit = await session.get(
        ProblemLanguageLimit,
        {"problem_id": contest_problem.id, "language_id": lang.id},
    )
    assert refreshed_limit is not None
    assert refreshed_limit.repetitions == 10


# ---------------------------------------------------------------------------
# Tests — get_test_case_id_map
# ---------------------------------------------------------------------------


async def test_get_test_case_id_map(
    engine,
    session: AsyncSession,
    contest_problem: Problem,
):
    tc1 = ProblemTestCase(problem_id=contest_problem.id, ordinal=1)
    tc2 = ProblemTestCase(problem_id=contest_problem.id, ordinal=2)
    tc3 = ProblemTestCase(problem_id=contest_problem.id, ordinal=3)
    session.add_all([tc1, tc2, tc3])
    await session.flush()
    await session.commit()

    async with open_db(engine) as db:
        mapping = await db.get_test_case_id_map(contest_problem.id)

    assert len(mapping) == 3
    assert mapping[1] == tc1.id
    assert mapping[2] == tc2.id
    assert mapping[3] == tc3.id


async def test_get_test_case_id_map_empty(engine, session: AsyncSession):
    async with open_db(engine) as db:
        mapping = await db.get_test_case_id_map(_uid())
    assert mapping == {}


# ---------------------------------------------------------------------------
# Tests — insert_test_result
# ---------------------------------------------------------------------------


async def test_insert_test_result(
    engine,
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
    team_user: User,
):
    lang = _make_language(session)
    await session.flush()
    sub = _make_submission(session, contest_problem, team_user, lang)
    await session.flush()
    j = _make_judgment(session, sub)
    await session.flush()
    tc = ProblemTestCase(problem_id=contest_problem.id, ordinal=1)
    session.add(tc)
    await session.flush()
    await session.commit()
    await session.close()

    async with open_db(engine) as db:
        await db.insert_test_result(
            judgment_id=j.id,
            test_case_id=tc.id,
            verdict=Verdict.AC,
            wall_time_ms=42,
            memory_kb=1024,
            exit_code=0,
            stdout_excerpt=b"hello\n",
            stderr_excerpt=b"",
        )

    from sqlalchemy import select

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        rows = (await vs.execute(select(SubmissionTestResult))).scalars().all()
        assert len(rows) == 1
        tr = rows[0]
        assert tr.judgment_id == j.id
        assert tr.test_case_id == tc.id
        assert tr.verdict == Verdict.AC
        assert tr.wall_time_ms == 42
        assert tr.stdout_excerpt == "hello\n"


# ---------------------------------------------------------------------------
# Tests — status transitions
# ---------------------------------------------------------------------------


async def test_set_judgment_dispatched(
    engine,
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
    team_user: User,
):
    lang = _make_language(session)
    await session.flush()
    sub = _make_submission(session, contest_problem, team_user, lang)
    await session.flush()
    j = _make_judgment(session, sub)
    await session.flush()
    await session.commit()
    j_id = j.id
    await session.close()

    async with open_db(engine) as db:
        await db.set_judgment_dispatched(j_id, "worker-test-1")

    from sqlalchemy import select

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        j_reloaded = await vs.get(SubmissionJudgment, j_id)
        assert j_reloaded.status == JudgmentStatus.DISPATCHED
        assert j_reloaded.worker_id == "worker-test-1"
        assert j_reloaded.started_at is not None

        audits = (
            (await vs.execute(select(SubmissionJudgmentAudit).where(SubmissionJudgmentAudit.judgment_id == j_id)))
            .scalars()
            .all()
        )
        worker_audits = [a for a in audits if a.event_source == "WORKER"]
        assert len(worker_audits) == 1
        assert worker_audits[0].to_status == JudgmentStatus.DISPATCHED


async def test_set_judgment_judging(
    engine,
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
    team_user: User,
):
    lang = _make_language(session)
    await session.flush()
    sub = _make_submission(session, contest_problem, team_user, lang)
    await session.flush()
    j = _make_judgment(session, sub, status=JudgmentStatus.DISPATCHED)
    await session.flush()
    await session.commit()
    j_id = j.id
    await session.close()

    async with open_db(engine) as db:
        await db.set_judgment_judging(j_id)

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        j_reloaded = await vs.get(SubmissionJudgment, j_id)
        assert j_reloaded.status == JudgmentStatus.JUDGING


async def test_set_judgment_done_autojudge_only(
    engine,
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
    team_user: User,
):
    lang = _make_language(session)
    await session.flush()
    sub = _make_submission(session, contest_problem, team_user, lang)
    await session.flush()
    j = _make_judgment(session, sub, status=JudgmentStatus.JUDGING)
    await session.flush()
    await session.commit()
    j_id = j.id
    await session.close()

    async with open_db(engine) as db:
        await db.set_judgment_done(
            j_id,
            verdict=Verdict.AC,
            autojudge_only=True,
            max_wall_time_ms=100,
            max_memory_kb=2048,
        )

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        j_reloaded = await vs.get(SubmissionJudgment, j_id)
        assert j_reloaded.status == JudgmentStatus.DONE
        assert j_reloaded.autojudge_verdict == Verdict.AC
        assert j_reloaded.final_verdict == Verdict.AC
        assert j_reloaded.finished_at is not None


async def test_create_balloon_task_if_needed_marks_first_autojudge_solve(
    engine,
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
    team_user: User,
    another_team_user: User,
):
    lang = _make_language(session)
    await session.flush()
    first_sub = _make_submission(session, contest_problem, team_user, lang, source="print('first')")
    first_sub.timestamp_seconds = 10 * 60
    second_sub = _make_submission(session, contest_problem, another_team_user, lang, source="print('second')")
    second_sub.timestamp_seconds = 20 * 60
    await session.flush()
    first_judgment = _make_judgment(session, first_sub, status=JudgmentStatus.JUDGING)
    second_judgment = _make_judgment(session, second_sub, status=JudgmentStatus.JUDGING)
    await session.flush()
    await session.commit()
    first_judgment_id = first_judgment.id
    second_judgment_id = second_judgment.id
    await session.close()

    async with open_db(engine) as db:
        first_payload = await db.get_submission_for_judging(first_judgment_id)
        second_payload = await db.get_submission_for_judging(second_judgment_id)
        await db.set_judgment_done(first_judgment_id, verdict=Verdict.AC, autojudge_only=True)
        await db.create_balloon_task_if_needed(first_payload, Verdict.AC)
        await db.set_judgment_done(second_judgment_id, verdict=Verdict.AC, autojudge_only=True)
        await db.create_balloon_task_if_needed(second_payload, Verdict.AC)

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        rows = (
            await vs.execute(
                select(Task).where(Task.problem_id == contest_problem.id).order_by(Task.created_timestamp_seconds)
            )
        ).scalars()
        tasks = list(rows.all())
        assert [task.type for task in tasks] == [TaskType.FIRST_BALLOON, TaskType.BALLOON]


async def test_set_judgment_done_human_review(
    engine,
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
    team_user: User,
):
    lang = _make_language(session)
    await session.flush()
    sub = _make_submission(session, contest_problem, team_user, lang)
    await session.flush()
    j = _make_judgment(session, sub, status=JudgmentStatus.JUDGING)
    await session.flush()
    await session.commit()
    j_id = j.id
    await session.close()

    async with open_db(engine) as db:
        await db.set_judgment_done(
            j_id,
            verdict=Verdict.WA,
            autojudge_only=False,
        )

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        j_reloaded = await vs.get(SubmissionJudgment, j_id)
        assert j_reloaded.autojudge_verdict == Verdict.WA
        assert j_reloaded.final_verdict is None  # awaiting human review


async def test_set_judgment_failed(
    engine,
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
    team_user: User,
):
    lang = _make_language(session)
    await session.flush()
    sub = _make_submission(session, contest_problem, team_user, lang)
    await session.flush()
    j = _make_judgment(session, sub)
    await session.flush()
    await session.commit()
    j_id = j.id
    await session.close()

    async with open_db(engine) as db:
        await db.set_judgment_failed(j_id, "Internal error: Docker timeout")

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        j_reloaded = await vs.get(SubmissionJudgment, j_id)
        assert j_reloaded.status == JudgmentStatus.FAILED
        assert j_reloaded.error_message == "Internal error: Docker timeout"
        assert j_reloaded.finished_at is not None


# ---------------------------------------------------------------------------
# Tests — list_languages
# ---------------------------------------------------------------------------


async def test_list_languages(engine, session: AsyncSession):
    _make_language(session, "python3")
    _make_inactive_language(session)
    await session.flush()
    await session.commit()

    async with open_db(engine) as db:
        langs = await db.list_languages()

    assert len(langs) == 1
    assert langs[0]["id"] == "python3"
    assert langs[0]["active"] is True


async def test_update_language_images(engine, session: AsyncSession):
    lang = _make_language(session, "python3")
    await session.flush()
    await session.commit()

    compile_image = "ghcr.io/dclobato/noca/judge-python3:compile-v5.0.0"
    run_image = "ghcr.io/dclobato/noca/judge-python3:run-v5.0.0"

    async with open_db(engine) as db:
        await db.update_language_images(
            lang.id,
            compile_image=compile_image,
            run_image=run_image,
        )

    async with async_sessionmaker(engine, expire_on_commit=False)() as verify_session:
        refreshed = await verify_session.get(Language, lang.id)
        assert refreshed is not None
        assert refreshed.compile_image == compile_image
        assert refreshed.run_image == run_image
