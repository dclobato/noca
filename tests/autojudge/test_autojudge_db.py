#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Tests for autojudge.db — web submission loading and the judgment state machine.

Uses the conftest ``engine`` fixture (in-memory SQLite with the full web ORM
schema). The autojudge Core tables map onto the same physical tables created by
``Base.metadata.create_all``, so Core queries work directly.

Arena-domain accessors live in ``test_autojudge_db_arena.py``; problem limits,
profiling, and the language registry in ``test_autojudge_db_limits.py``.
"""

from __future__ import annotations

import pytest
from _autojudge_db_seeds import (
    TEST_ATTEMPT_TOKEN,
    _make_judgment,
    _make_language,
    _make_submission,
    _uid,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autojudge.db import open_db
from autojudge.types import JobNotDispatchable, JudgmentOwnershipLost
from shared.enumerations import (
    JudgmentStatus,
    TaskType,
    Verdict,
)
from web.models.contest import Contest, Task
from web.models.problem import Problem, ProblemTestCase
from web.models.submission import (
    SubmissionJudgment,
    SubmissionJudgmentAudit,
    SubmissionTestResult,
)
from web.models.users import User

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
        await db.set_judgment_dispatched(
            j.id,
            "worker-retry",
            TEST_ATTEMPT_TOKEN,
            contest_start_time=running_contest.start_time,
        )

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


async def test_set_judgment_dispatched_is_fenced_on_a_terminal_judgment(
    engine,
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
    team_user: User,
):
    """A dispatch racing with the judgment's own completion must change nothing.

    Without the fence the late worker resets the row and deletes the verdict's
    test results, which is how a duplicated job corrupts a finished judgment.
    """
    lang = _make_language(session)
    await session.flush()
    sub = _make_submission(session, contest_problem, team_user, lang)
    await session.flush()
    j = _make_judgment(session, sub, status=JudgmentStatus.DONE)
    j.autojudge_verdict = Verdict.AC
    j.final_verdict = Verdict.AC
    j.worker_id = "worker-that-finished"
    await session.flush()

    tc = ProblemTestCase(problem_id=contest_problem.id, ordinal=1)
    session.add(tc)
    await session.flush()
    session.add(
        SubmissionTestResult(
            judgment_id=j.id,
            test_case_id=tc.id,
            verdict=Verdict.AC,
            wall_time_ms=10,
            memory_kb=20,
            exit_code=0,
        )
    )
    await session.commit()

    async with open_db(engine) as db:
        with pytest.raises(JobNotDispatchable):
            await db.set_judgment_dispatched(
                j.id,
                "late-worker",
                TEST_ATTEMPT_TOKEN,
                contest_start_time=running_contest.start_time,
            )

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        j_reloaded = await vs.get(SubmissionJudgment, j.id)
        assert j_reloaded is not None
        assert j_reloaded.status == JudgmentStatus.DONE
        assert j_reloaded.autojudge_verdict == Verdict.AC
        assert j_reloaded.worker_id == "worker-that-finished"

        results = (
            await vs.execute(SubmissionTestResult.__table__.select().where(SubmissionTestResult.judgment_id == j.id))
        ).all()
        assert len(results) == 1, "the finished judgment's results were deleted"


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
            exit_signal=None,
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


async def test_insert_test_result_accepts_remeasured_replay(
    engine,
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
    team_user: User,
):
    """A duplicate dispatch re-runs the case; drifting measurements must not fail it."""
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

    values = {
        "judgment_id": j.id,
        "test_case_id": tc.id,
        "verdict": Verdict.WA,
        "wall_time_ms": 42,
        "memory_kb": 1024,
        "exit_code": 0,
        "exit_signal": None,
        "stdout_excerpt": b"wrong\n",
        "stderr_excerpt": b"",
    }

    async with open_db(engine) as db:
        await db.insert_test_result(**values)
        await db.insert_test_result(**(values | {"wall_time_ms": 47, "memory_kb": 1100}))
        with pytest.raises(RuntimeError, match=r"different columns: verdict"):
            await db.insert_test_result(**(values | {"verdict": Verdict.RE}))

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        rows = (await vs.execute(select(SubmissionTestResult))).scalars().all()
        assert len(rows) == 1
        assert rows[0].verdict == Verdict.WA
        assert rows[0].wall_time_ms == 42


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
        await db.set_judgment_dispatched(j_id, "worker-test-1", TEST_ATTEMPT_TOKEN)

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
        await db.set_judgment_judging(j_id, TEST_ATTEMPT_TOKEN)

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
            attempt_token=TEST_ATTEMPT_TOKEN,
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
        await db.set_judgment_done(
            first_judgment_id, verdict=Verdict.AC, autojudge_only=True, attempt_token=TEST_ATTEMPT_TOKEN
        )
        await db.create_balloon_task_if_needed(first_payload, Verdict.AC)
        await db.set_judgment_done(
            second_judgment_id, verdict=Verdict.AC, autojudge_only=True, attempt_token=TEST_ATTEMPT_TOKEN
        )
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
            attempt_token=TEST_ATTEMPT_TOKEN,
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


async def test_set_judgment_failed_does_not_overwrite_terminal(
    engine,
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
    team_user: User,
):
    """A losing duplicate attempt must not bury a verdict another attempt committed."""
    lang = _make_language(session)
    await session.flush()
    sub = _make_submission(session, contest_problem, team_user, lang)
    await session.flush()
    j = _make_judgment(session, sub, status=JudgmentStatus.DONE)
    j.autojudge_verdict = Verdict.AC
    await session.flush()
    await session.commit()
    j_id = j.id
    await session.close()

    error_message = "Internal error: duplicate dispatch"
    async with open_db(engine) as db:
        await db.set_judgment_failed(j_id, error_message)

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        j_reloaded = await vs.get(SubmissionJudgment, j_id)
        assert j_reloaded.status == JudgmentStatus.DONE
        assert j_reloaded.autojudge_verdict == Verdict.AC
        assert j_reloaded.error_message is None
        audit_messages = (
            (
                await vs.execute(
                    select(SubmissionJudgmentAudit.message).where(SubmissionJudgmentAudit.judgment_id == j_id)
                )
            )
            .scalars()
            .all()
        )
        assert error_message not in audit_messages


async def test_insert_test_result_after_redispatch_wipe_is_discarded(
    engine,
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
    team_user: User,
):
    """A stale attempt's late case row must not mix into the new attempt's results.

    The replacement attempt's dispatch wipes the rows and claims the judgment
    before the slow original attempt inserts, so there is no unique violation to
    catch it — only the claim can.
    """
    lang = _make_language(session)
    await session.flush()
    sub = _make_submission(session, contest_problem, team_user, lang)
    await session.flush()
    j = _make_judgment(session, sub, status=JudgmentStatus.JUDGING)
    await session.flush()
    tc = ProblemTestCase(problem_id=contest_problem.id, ordinal=1)
    session.add(tc)
    await session.flush()
    await session.commit()
    j_id, tc_id = j.id, tc.id
    await session.close()

    values = {
        "judgment_id": j_id,
        "test_case_id": tc_id,
        "verdict": Verdict.WA,
        "wall_time_ms": 42,
        "memory_kb": 1024,
        "exit_code": 0,
        "exit_signal": None,
        "stdout_excerpt": b"wrong\n",
        "stderr_excerpt": b"",
        "attempt_token": TEST_ATTEMPT_TOKEN,
    }

    async with open_db(engine) as db:
        await db.insert_test_result(**values)
        await db.set_judgment_dispatched(j_id, "replacement-worker", "replacement:attempt")
        with pytest.raises(JudgmentOwnershipLost):
            await db.insert_test_result(**values)

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        rows = (
            (await vs.execute(select(SubmissionTestResult).where(SubmissionTestResult.judgment_id == j_id)))
            .scalars()
            .all()
        )

    assert rows == [], "the stale attempt poisoned the new attempt's result set"


async def test_set_judgment_done_after_takeover_is_discarded(
    engine,
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
    team_user: User,
):
    """A stale attempt finishing late must not overwrite the new owner's judgment."""
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
        await db.set_judgment_dispatched(j_id, "replacement-worker", "replacement:attempt")
        with pytest.raises(JudgmentOwnershipLost):
            await db.set_judgment_done(
                j_id,
                verdict=Verdict.AC,
                autojudge_only=True,
                attempt_token=TEST_ATTEMPT_TOKEN,
            )

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        j_reloaded = await vs.get(SubmissionJudgment, j_id)
        terminal_audits = (
            (
                await vs.execute(
                    select(SubmissionJudgmentAudit).where(
                        SubmissionJudgmentAudit.judgment_id == j_id,
                        SubmissionJudgmentAudit.to_status == JudgmentStatus.DONE,
                    )
                )
            )
            .scalars()
            .all()
        )

    assert j_reloaded.status == JudgmentStatus.DISPATCHED
    assert j_reloaded.autojudge_verdict is None
    assert terminal_audits == [], "the discarded attempt wrote a terminal audit row"


async def test_set_judgment_failed_after_takeover_is_discarded(
    engine,
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
    team_user: User,
):
    """A stale attempt's failure belongs to work another attempt now owns."""
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
        await db.set_judgment_dispatched(j_id, "replacement-worker", "replacement:attempt")
        await db.set_judgment_failed(j_id, "stale attempt blew up", attempt_token=TEST_ATTEMPT_TOKEN)

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        j_reloaded = await vs.get(SubmissionJudgment, j_id)

    assert j_reloaded.status == JudgmentStatus.DISPATCHED
    assert j_reloaded.error_message is None
