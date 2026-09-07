#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Tests for autojudge.db — problem limits, profiling runs, and the language
registry accessors the worker reads at start-up.
"""

from __future__ import annotations

import hashlib

import pytest
from _autojudge_db_seeds import (
    TEST_ATTEMPT_TOKEN,
    _make_inactive_language,
    _make_language,
    _uid,
)
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autojudge.db import ProfilingObservedLimits, open_db
from autojudge.types import JobNotDispatchable, JudgmentOwnershipLost
from shared.db_schema import contest_languages as contest_languages_table
from shared.db_schema import profiling_case_results
from shared.enumerations import (
    ProfilingStatus,
    Verdict,
)
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem, ProblemLanguageLimit, ProfilingRun

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


async def test_get_problem_effective_limits_by_language(
    engine,
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
):
    cpp = _make_language(session, "cpp")
    java = _make_language(session, "java")
    await session.flush()
    await session.execute(
        insert(contest_languages_table),
        [
            {"contest_id": running_contest.id, "language_id": cpp.id},
            {"contest_id": running_contest.id, "language_id": java.id},
        ],
    )
    session.add(
        ProblemLanguageLimit(
            problem_id=contest_problem.id,
            language_id=cpp.id,
            time_limit_ms=5000,
            memory_limit_kb=512000,
            pids_limit=128,
            output_limit_in_bytes=4096,
            repetitions=7,
        )
    )
    await session.flush()
    await session.commit()

    async with open_db(engine) as db:
        limits = await db.get_problem_effective_limits_by_language(contest_problem.id)

    assert list(limits) == ["cpp", "java"]
    assert limits["cpp"].time_limit_ms == 5000
    assert limits["cpp"].memory_limit_kb == 512000
    assert limits["cpp"].pids_limit == 128
    assert limits["cpp"].output_limit_in_bytes == 4096
    assert limits["cpp"].repetitions == 7
    assert limits["java"].time_limit_ms == contest_problem.time_limit_ms
    assert limits["java"].memory_limit_kb == contest_problem.memory_limit_kb
    assert limits["java"].pids_limit == contest_problem.pids_limit
    assert limits["java"].output_limit_in_bytes == contest_problem.output_limit_in_bytes
    assert limits["java"].repetitions == 1


async def test_get_problem_effective_limits_by_language_missing_raises(engine, session: AsyncSession):
    async with open_db(engine) as db:
        with pytest.raises(LookupError, match="not found"):
            await db.get_problem_effective_limits_by_language(_uid())


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
        attempt_token=TEST_ATTEMPT_TOKEN,
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
            attempt_token=TEST_ATTEMPT_TOKEN,
        )

    refreshed_limit = await session.get(
        ProblemLanguageLimit,
        {"problem_id": contest_problem.id, "language_id": lang.id},
    )
    assert refreshed_limit is not None
    assert refreshed_limit.repetitions == 10


async def test_set_profiling_running_freezes_the_repetition_count(
    engine,
    session: AsyncSession,
    contest_problem: Problem,
):
    """The count a run measures across is stamped when it starts, not read back later.

    An Auto-Limit suggestion has to be divisible by the number the observation
    was summed over. Deriving that later from the language registry would drift
    the moment the registry's default is edited, so the run records its own.
    """
    lang = _make_language(session, "cpp")
    profiling_run = ProfilingRun(
        problem_id=contest_problem.id,
        language_id=lang.id,
        source_code="int main() { return 0; }",
        source_hash=hashlib.sha256(b"int main() { return 0; }").hexdigest(),
        status=ProfilingStatus.DISPATCHED,
        safety_factor=1.5,
        attempt_token=TEST_ATTEMPT_TOKEN,
    )
    session.add_all([lang, profiling_run])
    await session.flush()
    await session.commit()

    async with open_db(engine) as db:
        await db.set_profiling_running(profiling_run.id, TEST_ATTEMPT_TOKEN, 7)

    await session.refresh(profiling_run)
    assert profiling_run.status is ProfilingStatus.RUNNING
    assert profiling_run.repetitions == 7


async def test_set_profiling_dispatched_is_fenced_on_a_terminal_run(
    engine,
    session: AsyncSession,
    contest_problem: Problem,
):
    """A late dispatch must not reopen a finished profiling run."""
    lang = _make_language(session, "cpp-fence")
    profiling_run = ProfilingRun(
        problem_id=contest_problem.id,
        language_id=lang.id,
        source_code="int main() { return 0; }",
        source_hash=hashlib.sha256(b"int main() { return 0; }").hexdigest(),
        status=ProfilingStatus.FAILED,
        safety_factor=1.5,
        error_message="original failure",
    )
    session.add_all([lang, profiling_run])
    await session.flush()
    await session.commit()

    async with open_db(engine) as db:
        with pytest.raises(JobNotDispatchable):
            await db.set_profiling_dispatched(profiling_run.id, "late-worker", TEST_ATTEMPT_TOKEN)

    await session.refresh(profiling_run)
    assert profiling_run.status == ProfilingStatus.FAILED
    assert profiling_run.error_message == "original failure"


async def test_set_profiling_done_after_takeover_publishes_no_limits(
    engine,
    session: AsyncSession,
    contest_problem: Problem,
):
    """A stale attempt must not publish its profiled limits over the new owner's.

    The limits are the whole product of a profiling run, so losing the claim has
    to stop the ``problem_language_limits`` write too, not just the status.
    """
    lang = _make_language(session, "cpp-claim")
    profiling_run = ProfilingRun(
        problem_id=contest_problem.id,
        language_id=lang.id,
        source_code="int main() { return 0; }",
        source_hash=hashlib.sha256(b"int main() { return 0; }").hexdigest(),
        status=ProfilingStatus.RUNNING,
        safety_factor=1.5,
        attempt_token="replacement:attempt",
    )
    session.add_all([lang, profiling_run])
    await session.flush()
    await session.commit()
    run_id, language_id = profiling_run.id, lang.id

    async with open_db(engine) as db:
        with pytest.raises(JudgmentOwnershipLost):
            await db.set_profiling_done(
                run_id,
                ProfilingObservedLimits(
                    time_limit_ms=999,
                    memory_limit_kb=9999,
                    pids_limit=99,
                    output_limit_in_bytes=9999,
                ),
                10,
                attempt_token=TEST_ATTEMPT_TOKEN,
            )

    async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
        published = await verify.get(ProblemLanguageLimit, (contest_problem.id, language_id))
        refreshed = await verify.get(ProfilingRun, run_id)

    assert published is None, "the discarded attempt published its limits"
    assert refreshed is not None
    assert refreshed.status == ProfilingStatus.RUNNING


async def test_insert_profiling_case_result_after_takeover_is_discarded(
    engine,
    session: AsyncSession,
    contest_problem: Problem,
):
    """Without a unique key, only the claim keeps a stale row out of the results."""
    lang = _make_language(session, "cpp-case-claim")
    profiling_run = ProfilingRun(
        problem_id=contest_problem.id,
        language_id=lang.id,
        source_code="int main() { return 0; }",
        source_hash=hashlib.sha256(b"int main() { return 0; }").hexdigest(),
        status=ProfilingStatus.RUNNING,
        safety_factor=1.5,
        attempt_token="replacement:attempt",
    )
    session.add_all([lang, profiling_run])
    await session.flush()
    await session.commit()
    run_id = profiling_run.id

    async with open_db(engine) as db:
        with pytest.raises(JudgmentOwnershipLost):
            await db.insert_profiling_case_result(
                profiling_run_id=run_id,
                test_case_id=_uid(),
                ordinal=1,
                verdict=Verdict.AC,
                total_wall_time_ms=10,
                peak_memory_kb=100,
                peak_output_bytes=2,
                peak_pids=1,
                exit_code=0,
                attempt_token=TEST_ATTEMPT_TOKEN,
            )

    async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
        rows = (
            (
                await verify.execute(
                    select(profiling_case_results).where(profiling_case_results.c.profiling_run_id == run_id)
                )
            )
            .mappings()
            .all()
        )

    assert rows == []


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
