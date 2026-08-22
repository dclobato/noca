#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Worker-side database behaviour for solution-test runs.

Exercises the real accessors through ``open_db`` rather than a fake, so retry
idempotency and interactive persistence are proven rather than asserted.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from _autojudge_db_seeds import TEST_ATTEMPT_TOKEN
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autojudge.db import open_db
from autojudge.types import JobNotDispatchable, JudgmentOwnershipLost
from shared.db_schema import solution_test_case_results, solution_test_runs
from shared.enumerations import CustomValidatorCrashReason, JudgmentStatus, Verdict
from web.models.language import Language
from web.models.problem import Problem, ProblemTestCase

pytestmark = pytest.mark.asyncio

LANGUAGE_ID = "python3"


def _make_language(session: AsyncSession) -> Language:
    language = Language(
        id=LANGUAGE_ID,
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
    session.add(language)
    return language


async def _seed_run(session: AsyncSession, problem: Problem) -> str:
    """Insert one QUEUED run against the given problem and return its id."""
    _make_language(session)
    await session.flush()
    run_id = str(uuid.uuid4())
    await session.execute(
        solution_test_runs.insert().values(
            id=run_id,
            problem_id=problem.id,
            language_id=LANGUAGE_ID,
            source_code="print('x')",
            source_hash="0" * 64,
            source_size_bytes=12,
            status=JudgmentStatus.QUEUED,
            attempt_token=TEST_ATTEMPT_TOKEN,
            triggered_by_label="judge-1",
        )
    )
    await session.commit()
    return run_id


def _attempt_result(
    verdict: Verdict | None,
    *,
    validator_exit_code: int | None = 0,
    validator_signal: int | None = None,
    validator_stderr_excerpt: bytes = b"",
    crash_reason: Any = None,
    watchdog_stalled_side: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        classification=SimpleNamespace(verdict=verdict),
        contestant_exit_code=0,
        contestant_signal=None,
        validator_exit_code=validator_exit_code,
        validator_signal=validator_signal,
        transcript=SimpleNamespace(as_dict=lambda: {"lines": [{"dir": "user", "line": "7"}], "truncated": False}),
        contestant_stderr_excerpt=b"",
        validator_stderr_excerpt=validator_stderr_excerpt,
        contestant_output_bytes=2,
        crash_reason=crash_reason,
        wall_time_ms=5,
        memory_kb=50,
        watchdog_stalled_side=watchdog_stalled_side,
    )


def _owned_by(run_id: str) -> Any:
    return solution_test_case_results.c.solution_test_run_id == run_id


async def _cases(session: AsyncSession, run_id: str) -> list[Any]:
    return (
        (await session.execute(select(solution_test_case_results).where(_owned_by(run_id)))).mappings().all()  # type: ignore[return-value]
    )


async def _run_row(session: AsyncSession, run_id: str) -> Any:
    return (await session.execute(select(solution_test_runs).where(solution_test_runs.c.id == run_id))).mappings().one()


async def test_dispatch_clears_prior_case_results(
    engine,
    session: AsyncSession,
    judgeable_contest_problem: Problem,
) -> None:
    """Re-dispatch must wipe the previous attempt's rows.

    Without it the retry's inserts would collide with the ordinary partial
    unique index on (run, ordinal).
    """
    run_id = await _seed_run(session, judgeable_contest_problem)

    async with open_db(engine) as db:
        await db.set_solution_test_dispatched(run_id, "worker-1", TEST_ATTEMPT_TOKEN)
        await db.insert_solution_test_case_result(
            solution_test_run_id=run_id,
            test_case_id=None,
            ordinal=1,
            verdict=Verdict.WA,
            input_excerpt=b"problem input",
            expected_output_excerpt=b"expected output",
            stdout_excerpt=b"submission output",
        )

    first_attempt = (await _cases(session, run_id))[0]
    assert first_attempt["input_excerpt"] == "problem input"
    assert first_attempt["expected_output_excerpt"] == "expected output"
    assert first_attempt["stdout_excerpt"] == "submission output"

    async with open_db(engine) as db:
        await db.set_solution_test_dispatched(run_id, "worker-2", TEST_ATTEMPT_TOKEN)
        assert await _cases(session, run_id) == [], "stale rows survived re-dispatch"
        await db.insert_solution_test_case_result(
            solution_test_run_id=run_id,
            test_case_id=None,
            ordinal=1,
            verdict=Verdict.AC,
        )

    rows = await _cases(session, run_id)
    assert [row["verdict"] for row in rows] == [Verdict.AC]

    run = await _run_row(session, run_id)
    assert run["status"] == JudgmentStatus.DISPATCHED
    assert run["worker_id"] == "worker-2"
    assert run["verdict"] is None


async def test_dispatch_is_fenced_on_a_terminal_run(
    engine,
    session: AsyncSession,
    judgeable_contest_problem: Problem,
) -> None:
    """A finished run must survive a late dispatch intact.

    Every solution test is a fresh QUEUED row, so a dispatch arriving at a DONE
    run is a duplicate — a requeue that raced with the run's own completion. It
    must not reset the row or delete the results it already produced.
    """
    run_id = await _seed_run(session, judgeable_contest_problem)

    async with open_db(engine) as db:
        await db.set_solution_test_dispatched(run_id, "worker-1", TEST_ATTEMPT_TOKEN)
        await db.insert_solution_test_case_result(
            solution_test_run_id=run_id,
            test_case_id=None,
            ordinal=1,
            verdict=Verdict.AC,
        )
        await db.set_solution_test_done(
            run_id,
            verdict=Verdict.AC,
            attempt_token=TEST_ATTEMPT_TOKEN,
            max_wall_time_ms=10,
            max_memory_kb=100,
        )

        with pytest.raises(JobNotDispatchable):
            await db.set_solution_test_dispatched(run_id, "worker-2", TEST_ATTEMPT_TOKEN)

    run = await _run_row(session, run_id)
    assert run["status"] == JudgmentStatus.DONE
    assert run["verdict"] == Verdict.AC
    assert run["worker_id"] == "worker-1"
    assert run["max_wall_time_ms"] == 10
    assert run["finished_at"] is not None
    assert [row["verdict"] for row in await _cases(session, run_id)] == [Verdict.AC]


async def test_interactive_attempt_resolves_its_test_case_id(
    engine,
    session: AsyncSession,
    judgeable_contest_problem: Problem,
) -> None:
    """A live case must not be recorded as NULL — the UI reads NULL as 'since deleted'."""
    run_id = await _seed_run(session, judgeable_contest_problem)
    expected_id = await session.scalar(
        select(ProblemTestCase.id).where(
            ProblemTestCase.problem_id == judgeable_contest_problem.id,
            ProblemTestCase.ordinal == 1,
        )
    )

    async with open_db(engine) as db:
        await db.insert_interactive_attempt(
            domain="contest",
            owner_id=run_id,
            attempt_number=1,
            test_case_ordinal=1,
            result=_attempt_result(Verdict.WA, validator_exit_code=1, validator_stderr_excerpt=b"bad output"),
            attempt_target="solution_test",
        )

    row = (await _cases(session, run_id))[0]
    assert row["test_case_id"] == expected_id
    assert row["attempt_number"] == 1
    assert row["ordinal"] == 1
    assert row["verdict"] == Verdict.WA
    assert row["transcript"] == {"lines": [{"dir": "user", "line": "7"}], "truncated": False}
    # A clean validator exit (no crash) is persisted as validator_verdict, not crash_reason.
    assert row["validator_exit_code"] == 1
    assert row["validator_signal"] is None
    assert row["validator_stderr_excerpt"] == "bad output"
    assert row["validator_verdict"] == Verdict.WA
    assert row["crash_reason"] is None
    assert row["limit_outcome"] is None


@pytest.mark.parametrize(
    ("result", "expected_crash_reason", "expected_validator_verdict", "expected_limit_outcome"),
    [
        pytest.param(
            _attempt_result(Verdict.RE, validator_exit_code=None, crash_reason=CustomValidatorCrashReason.SIGNAL),
            CustomValidatorCrashReason.SIGNAL,
            None,
            None,
            id="crashed_validator_has_no_clean_exit_reading",
        ),
        pytest.param(
            _attempt_result(Verdict.MLE),
            None,
            Verdict.AC,
            "MLE",
            id="mle_limit_outcome_recorded",
        ),
        pytest.param(
            _attempt_result(Verdict.TLE, watchdog_stalled_side="contestant"),
            None,
            Verdict.AC,
            "TLE",
            id="contestant_watchdog_tle_limit_outcome_recorded",
        ),
        pytest.param(
            _attempt_result(Verdict.TLE, watchdog_stalled_side="validator"),
            None,
            Verdict.AC,
            None,
            id="non_watchdog_tle_leaves_limit_outcome_null",
        ),
    ],
)
async def test_interactive_attempt_persists_outcome_fields(
    engine,
    session: AsyncSession,
    judgeable_contest_problem: Problem,
    result: SimpleNamespace,
    expected_crash_reason: CustomValidatorCrashReason | None,
    expected_validator_verdict: Verdict | None,
    expected_limit_outcome: str | None,
) -> None:
    """crash_reason, validator_verdict, and limit_outcome derive from the same
    attempt result the submission-side path already uses, so each combination
    is exercised once rather than duplicated across near-identical tests.

    The last case matters on its own: a TLE stall attributed to the *validator*
    side (not the contestant) is not an enforced contestant limit, so
    limit_outcome must stay NULL even though the case verdict is TLE.
    """
    run_id = await _seed_run(session, judgeable_contest_problem)

    async with open_db(engine) as db:
        await db.insert_interactive_attempt(
            domain="contest",
            owner_id=run_id,
            attempt_number=1,
            test_case_ordinal=1,
            result=result,
            attempt_target="solution_test",
        )

    row = (await _cases(session, run_id))[0]
    assert row["crash_reason"] == expected_crash_reason
    assert row["validator_verdict"] == expected_validator_verdict
    assert row["limit_outcome"] == expected_limit_outcome


async def test_interactive_attempt_for_a_missing_ordinal_stays_null(
    engine,
    session: AsyncSession,
    judgeable_contest_problem: Problem,
) -> None:
    """NULL is reserved for a case that genuinely is not there any more."""
    run_id = await _seed_run(session, judgeable_contest_problem)

    async with open_db(engine) as db:
        await db.insert_interactive_attempt(
            domain="contest",
            owner_id=run_id,
            attempt_number=1,
            test_case_ordinal=99,
            result=_attempt_result(Verdict.WA),
            attempt_target="solution_test",
        )

    assert (await _cases(session, run_id))[0]["test_case_id"] is None


async def test_attempt_one_clears_the_previous_cases_rows(
    engine,
    session: AsyncSession,
    judgeable_contest_problem: Problem,
) -> None:
    """A run retains only the attempts of the last executed case, like a judgment."""
    session.add(ProblemTestCase(problem_id=judgeable_contest_problem.id, ordinal=2, is_sample=False))
    run_id = await _seed_run(session, judgeable_contest_problem)

    async with open_db(engine) as db:
        for ordinal in (1, 2):
            await db.insert_interactive_attempt(
                domain="contest",
                owner_id=run_id,
                attempt_number=1,
                test_case_ordinal=ordinal,
                result=_attempt_result(Verdict.AC),
                attempt_target="solution_test",
            )

    assert [row["ordinal"] for row in await _cases(session, run_id)] == [2]


async def test_interactive_attempts_never_touch_submission_tables(
    engine,
    session: AsyncSession,
    judgeable_contest_problem: Problem,
) -> None:
    """attempt_target is the whole isolation guarantee for the interactive path."""
    from shared.db_schema import submission_interactive_attempts

    run_id = await _seed_run(session, judgeable_contest_problem)

    async with open_db(engine) as db:
        await db.insert_interactive_attempt(
            domain="contest",
            owner_id=run_id,
            attempt_number=1,
            test_case_ordinal=1,
            result=_attempt_result(Verdict.AC),
            attempt_target="solution_test",
        )

    leaked = (await session.execute(select(submission_interactive_attempts.c.id))).scalars().all()
    assert leaked == []
    assert len(await _cases(session, run_id)) == 1


async def test_fail_exhausted_only_terminalizes_a_non_terminal_run(
    engine,
    session: AsyncSession,
    judgeable_contest_problem: Problem,
) -> None:
    """The reconciler's tombstone handler must not overwrite a finished run."""
    run_id = await _seed_run(session, judgeable_contest_problem)

    async with open_db(engine) as db:
        assert await db.fail_exhausted_solution_test_run(run_id) is True

    run = await _run_row(session, run_id)
    assert run["status"] == JudgmentStatus.FAILED
    assert "repeated attempts" in run["error_message"]

    async with open_db(engine) as db:
        # Already terminal: a second pass changes nothing and reports no work done.
        assert await db.fail_exhausted_solution_test_run(run_id) is False


async def test_terminal_run_is_not_reloaded_for_judging(
    engine,
    session: AsyncSession,
    judgeable_contest_problem: Problem,
) -> None:
    """A duplicate dequeue of a finished run must not restart it."""
    run_id = await _seed_run(session, judgeable_contest_problem)

    async with open_db(engine) as db:
        loaded = await db.get_solution_test_run_for_judging(run_id)
        assert loaded.contest_id == judgeable_contest_problem.contest_id
        await db.set_solution_test_done(run_id, verdict=Verdict.AC, attempt_token=TEST_ATTEMPT_TOKEN)
        with pytest.raises(LookupError, match="not runnable"):
            await db.get_solution_test_run_for_judging(run_id)


async def test_case_result_after_redispatch_wipe_is_discarded(
    engine,
    session: AsyncSession,
    judgeable_contest_problem: Problem,
) -> None:
    """A stale attempt's late row must not join the new attempt's results.

    The replacement's dispatch wipes the rows and claims the run before the slow
    original attempt inserts, so there is no collision to catch it.
    """
    run_id = await _seed_run(session, judgeable_contest_problem)

    async with open_db(engine) as db:
        await db.set_solution_test_dispatched(run_id, "worker-1", TEST_ATTEMPT_TOKEN)
        await db.insert_solution_test_case_result(
            solution_test_run_id=run_id,
            test_case_id=None,
            ordinal=1,
            verdict=Verdict.WA,
            attempt_token=TEST_ATTEMPT_TOKEN,
        )
        # The reaper requeued the run and a replacement attempt took it over.
        await db.set_solution_test_dispatched(run_id, "replacement-worker", "replacement:attempt")
        with pytest.raises(JudgmentOwnershipLost):
            await db.insert_solution_test_case_result(
                solution_test_run_id=run_id,
                test_case_id=None,
                ordinal=2,
                verdict=Verdict.WA,
                attempt_token=TEST_ATTEMPT_TOKEN,
            )

    assert await _cases(session, run_id) == [], "the stale attempt poisoned the new attempt's results"


async def test_set_solution_test_done_after_takeover_is_discarded(
    engine,
    session: AsyncSession,
    judgeable_contest_problem: Problem,
) -> None:
    """A stale attempt finishing late must not overwrite the new owner's verdict."""
    run_id = await _seed_run(session, judgeable_contest_problem)

    async with open_db(engine) as db:
        await db.set_solution_test_dispatched(run_id, "replacement-worker", "replacement:attempt")
        with pytest.raises(JudgmentOwnershipLost):
            await db.set_solution_test_done(run_id, verdict=Verdict.AC, attempt_token=TEST_ATTEMPT_TOKEN)

    run = await _run_row(session, run_id)
    assert run["status"] == JudgmentStatus.DISPATCHED
    assert run["verdict"] is None


async def test_set_solution_test_failed_after_takeover_is_discarded(
    engine,
    session: AsyncSession,
    judgeable_contest_problem: Problem,
) -> None:
    """A stale attempt's failure belongs to work another attempt now owns."""
    run_id = await _seed_run(session, judgeable_contest_problem)

    async with open_db(engine) as db:
        await db.set_solution_test_dispatched(run_id, "replacement-worker", "replacement:attempt")
        await db.set_solution_test_failed(run_id, "stale attempt blew up", attempt_token=TEST_ATTEMPT_TOKEN)

    run = await _run_row(session, run_id)
    assert run["status"] == JudgmentStatus.DISPATCHED
    assert run["error_message"] is None
