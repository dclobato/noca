#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for idempotent Arena test-result persistence."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from _autojudge_db_seeds import TEST_ATTEMPT_TOKEN, _add_arena_tc, _make_arena_user, _make_language
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from arena.models.arena_notifications import ArenaNotification
from arena.models.arena_problems import ArenaProblem, ArenaTestCase
from arena.models.arena_submissions import (
    ArenaSubmission,
    ArenaSubmissionJudgment,
    ArenaSubmissionTestResult,
)
from autojudge.db import open_db
from autojudge.types import JudgmentOwnershipLost
from shared.enumerations import JudgmentStatus, Verdict


async def _seed_judgment(
    session: AsyncSession,
) -> tuple[ArenaSubmissionJudgment, ArenaTestCase]:
    """Create one judging Arena submission and its first test case."""
    language = _make_language(session, lang_id=f"arena-result-{uuid.uuid4().hex[:6]}")
    user = _make_arena_user(session)
    await session.flush()
    problem = ArenaProblem(
        arena_number=701,
        title="Arena result replay",
        owner_id=user.id,
        problem_statement="<p>Echo.</p>",
    )
    session.add(problem)
    await session.flush()
    test_case = _add_arena_tc(session, problem.id, 1)
    submission = ArenaSubmission(
        user_id=user.id,
        problem_id=problem.id,
        language_id=language.id,
        source_code="print('wrong')",
        source_hash="a" * 64,
        source_size_bytes=14,
    )
    session.add(submission)
    await session.flush()
    judgment = ArenaSubmissionJudgment(
        submission_id=submission.id,
        status=JudgmentStatus.JUDGING.value,
        attempt_token=TEST_ATTEMPT_TOKEN,
    )
    session.add(judgment)
    await session.commit()
    return judgment, test_case


def _result_values(judgment_id: str, test_case_id: str) -> dict[str, Any]:
    """Return a complete first-failure payload for the worker accessor."""
    return {
        "judgment_id": judgment_id,
        "test_case_id": test_case_id,
        "verdict": Verdict.WA,
        "wall_time_ms": 25,
        "memory_kb": 4096,
        "exit_code": 0,
        "exit_signal": None,
        "stdout_excerpt": b"wrong answer\n",
        "stderr_excerpt": b"",
        "attempt_token": TEST_ATTEMPT_TOKEN,
    }


async def test_insert_arena_test_result_accepts_identical_replay(
    engine: AsyncEngine,
    session: AsyncSession,
) -> None:
    """An identical duplicate result must remain one successful durable row."""
    judgment, test_case = await _seed_judgment(session)
    values = _result_values(judgment.id, test_case.id)

    async with open_db(engine) as db:
        await db.insert_arena_test_result(**values)
        await db.insert_arena_test_result(**values)

    async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
        result_count = await verify.scalar(
            select(func.count())
            .select_from(ArenaSubmissionTestResult)
            .where(ArenaSubmissionTestResult.judgment_id == judgment.id)
        )
        result = await verify.scalar(
            select(ArenaSubmissionTestResult).where(ArenaSubmissionTestResult.judgment_id == judgment.id)
        )

    assert result_count == 1
    assert result is not None
    assert result.test_case_id == test_case.id
    assert result.verdict == Verdict.WA.value
    assert result.stdout_excerpt == "wrong answer\n"


async def test_insert_arena_test_result_accepts_remeasured_replay(
    engine: AsyncEngine,
    session: AsyncSession,
) -> None:
    """A replay re-runs the program, so drifting measurements must not fail it."""
    judgment, test_case = await _seed_judgment(session)
    values = _result_values(judgment.id, test_case.id)

    async with open_db(engine) as db:
        await db.insert_arena_test_result(**values)
        await db.insert_arena_test_result(**(values | {"wall_time_ms": 31, "memory_kb": 4200}))

    async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
        result_count = await verify.scalar(
            select(func.count())
            .select_from(ArenaSubmissionTestResult)
            .where(ArenaSubmissionTestResult.judgment_id == judgment.id)
        )
        result = await verify.scalar(
            select(ArenaSubmissionTestResult).where(ArenaSubmissionTestResult.judgment_id == judgment.id)
        )

    assert result_count == 1
    assert result is not None
    assert result.verdict == Verdict.WA.value
    assert result.wall_time_ms == 25


async def test_set_arena_judgment_failed_does_not_overwrite_terminal(
    engine: AsyncEngine,
    session: AsyncSession,
) -> None:
    """A losing duplicate attempt must not bury a verdict another attempt committed."""
    judgment, _test_case = await _seed_judgment(session)
    judgment.status = JudgmentStatus.DONE.value
    judgment.autojudge_verdict = Verdict.WA.value
    judgment.final_verdict = Verdict.WA.value
    await session.commit()
    judgment_id = judgment.id

    async with open_db(engine) as db:
        await db.set_arena_judgment_failed(judgment_id, "Internal judge error: duplicate dispatch")

    async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
        reloaded = await verify.get(ArenaSubmissionJudgment, judgment_id)

    assert reloaded is not None
    assert reloaded.status == JudgmentStatus.DONE.value
    assert reloaded.final_verdict == Verdict.WA.value
    assert reloaded.error_message is None


async def test_insert_arena_test_result_rejects_conflicting_case(
    engine: AsyncEngine,
    session: AsyncSession,
) -> None:
    """A replay blaming a different test case is a real inconsistency."""
    judgment, test_case = await _seed_judgment(session)
    other_case = _add_arena_tc(session, test_case.problem_id, 2)
    await session.commit()
    values = _result_values(judgment.id, test_case.id)

    async with open_db(engine) as db:
        await db.insert_arena_test_result(**values)
        with pytest.raises(RuntimeError, match=r"different columns: test_case_id"):
            await db.insert_arena_test_result(**(values | {"test_case_id": other_case.id}))

    async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
        result = await verify.scalar(
            select(ArenaSubmissionTestResult).where(ArenaSubmissionTestResult.judgment_id == judgment.id)
        )

    assert result is not None
    assert result.test_case_id == test_case.id


async def test_insert_arena_test_result_rejects_conflicting_replay(
    engine: AsyncEngine,
    session: AsyncSession,
) -> None:
    """A divergent duplicate must not silently replace or reuse the first row."""
    judgment, test_case = await _seed_judgment(session)
    values = _result_values(judgment.id, test_case.id)

    async with open_db(engine) as db:
        await db.insert_arena_test_result(**values)
        with pytest.raises(RuntimeError, match=r"different columns: verdict"):
            await db.insert_arena_test_result(**(values | {"verdict": Verdict.RE}))

    async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
        result = await verify.scalar(
            select(ArenaSubmissionTestResult).where(ArenaSubmissionTestResult.judgment_id == judgment.id)
        )

    assert result is not None
    assert result.verdict == Verdict.WA.value


async def test_insert_arena_test_result_after_redispatch_wipe_is_discarded(
    engine: AsyncEngine,
    session: AsyncSession,
) -> None:
    """The stale attempt's late write must not land in the new attempt's results.

    This is the sequence a reaper requeue actually produces: the replacement
    attempt's dispatch wipes the result rows and claims the judgment, and only
    then does the slow original attempt reach its insert. There is nothing to
    collide with, so only the claim can stop the row.
    """
    judgment, test_case = await _seed_judgment(session)
    values = _result_values(judgment.id, test_case.id)

    async with open_db(engine) as db:
        await db.insert_arena_test_result(**values)
        # The reaper requeued the job and a replacement attempt took it over.
        await db.set_arena_judgment_dispatched(judgment.id, "replacement-worker", "replacement:attempt")
        with pytest.raises(JudgmentOwnershipLost):
            await db.insert_arena_test_result(**values)

    async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
        result_count = await verify.scalar(
            select(func.count())
            .select_from(ArenaSubmissionTestResult)
            .where(ArenaSubmissionTestResult.judgment_id == judgment.id)
        )

    assert result_count == 0, "the stale attempt poisoned the new attempt's result set"


async def test_insert_arena_test_result_after_takeover_is_discarded(
    engine: AsyncEngine,
    session: AsyncSession,
) -> None:
    """A lost claim stops the write even when the committed row is still there."""
    judgment, test_case = await _seed_judgment(session)
    values = _result_values(judgment.id, test_case.id)

    async with open_db(engine) as db:
        await db.insert_arena_test_result(**values)

    # A replacement attempt claims the judgment without clearing the rows.
    judgment.attempt_token = "replacement:attempt"
    await session.commit()

    async with open_db(engine) as db:
        with pytest.raises(JudgmentOwnershipLost):
            await db.insert_arena_test_result(**(values | {"verdict": Verdict.RE}))

    async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
        result = await verify.scalar(
            select(ArenaSubmissionTestResult).where(ArenaSubmissionTestResult.judgment_id == judgment.id)
        )

    assert result is not None
    assert result.verdict == Verdict.WA.value


async def test_set_arena_judgment_done_after_takeover_is_discarded(
    engine: AsyncEngine,
    session: AsyncSession,
) -> None:
    """A stale attempt finishing late must not overwrite the new owner's judgment."""
    judgment, _test_case = await _seed_judgment(session)
    judgment_id = judgment.id

    async with open_db(engine) as db:
        queued = await db.get_arena_submission_for_judging(judgment_id)
        await db.set_arena_judgment_dispatched(judgment_id, "replacement-worker", "replacement:attempt")
        with pytest.raises(JudgmentOwnershipLost):
            await db.set_arena_judgment_done(queued, Verdict.AC, attempt_token=TEST_ATTEMPT_TOKEN)

    async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
        reloaded = await verify.get(ArenaSubmissionJudgment, judgment_id)
        notifications = (
            await verify.execute(
                select(func.count()).select_from(ArenaNotification).where(ArenaNotification.source_ref == judgment_id)
            )
        ).scalar_one()

    assert reloaded is not None
    assert reloaded.status == JudgmentStatus.DISPATCHED.value
    assert reloaded.final_verdict is None
    assert notifications == 0, "the discarded attempt still notified the user"


async def test_set_arena_judgment_failed_after_takeover_is_discarded(
    engine: AsyncEngine,
    session: AsyncSession,
) -> None:
    """A stale attempt's failure belongs to work another attempt now owns."""
    judgment, _test_case = await _seed_judgment(session)
    judgment_id = judgment.id

    async with open_db(engine) as db:
        await db.set_arena_judgment_dispatched(judgment_id, "replacement-worker", "replacement:attempt")
        await db.set_arena_judgment_failed(judgment_id, "stale attempt blew up", TEST_ATTEMPT_TOKEN)

    async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
        reloaded = await verify.get(ArenaSubmissionJudgment, judgment_id)

    assert reloaded is not None
    assert reloaded.status == JudgmentStatus.DISPATCHED.value
    assert reloaded.error_message is None
