#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Tests for autojudge.worker — verdict outcomes of the submission pipeline.

These tests monkeypatch compile/run phases and container management so they run
without Docker. Database operations use the real in-memory SQLite engine (via
conftest fixtures); Valkey is replaced by a lightweight fake.

Failure and recovery paths live in ``test_autojudge_worker_failures.py``,
dispatch routing in ``test_autojudge_dispatch.py``, worker start-up in
``test_autojudge_worker_lifecycle.py``, and the consumer loop in
``test_autojudge_worker_loop.py``.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from _autojudge_worker_fakes import (
    _FakeValkey,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autojudge.db import open_db
from autojudge.runner import CompileResult, RunResult
from shared.enumerations import JudgmentStatus, Verdict
from shared.language_registry import default_language_registry
from web.models.submission import (
    SubmissionJudgment,
    SubmissionTestResult,
)


async def test_compile_error_sets_ce_verdict(
    engine,
    session: AsyncSession,
    seed_data,
    monkeypatch,
):
    """When compilation fails, judgment should be DONE with CE verdict."""
    j = seed_data["judgment"]
    sub = seed_data["submission"]
    j_id, sub_id = j.id, sub.id
    fake_valkey = _FakeValkey()

    async def _fake_compile(*args, **kwargs):
        return CompileResult(success=False, exit_code=1, compile_log="syntax error")

    monkeypatch.setattr("autojudge.submission_job.compile_submission", _fake_compile)

    from autojudge.db import QueuedSubmission
    from autojudge.worker import _process_job

    qs = QueuedSubmission(
        judgment_id=j_id,
        submission_id=sub_id,
        contest_id=seed_data["contest"].id,
        contest_start_time=seed_data["contest"].start_time,
        problem_id=seed_data["problem"].id,
        team_id=sub.team_id,
        language_id=sub.language_id,
        source_code=sub.source_code,
        autojudge_only=True,
        accept_pe=False,
        stop_updating_scoreboard=120,
    )
    await session.close()

    async with open_db(engine) as db:
        await _process_job(
            submission=qs,
            db=db,
            valkey=fake_valkey,
            pool_manager=AsyncMock(),
            language_registry=default_language_registry(),
            docker_client=AsyncMock(),
            executor=None,
            worker_id="test-worker",
        )

    from sqlalchemy import select as sa_select

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        j_reloaded = await vs.get(SubmissionJudgment, j_id)
        assert j_reloaded.status == JudgmentStatus.DONE
        assert j_reloaded.autojudge_verdict == Verdict.CE
        assert j_reloaded.compile_log == "syntax error"

        results = (await vs.execute(sa_select(SubmissionTestResult))).scalars().all()
        assert len(results) == 0

    # autojudge_only=True CE should publish a VerdictEvent
    assert len(fake_valkey.published) == 1
    _, payload = fake_valkey.published[0]
    event_payload = json.loads(payload)
    assert event_payload["verdict"] == Verdict.CE.value
    assert event_payload["update_kind"] == "autojudge"
    assert event_payload["team_id"] == sub.team_id
    assert event_payload["problem_id"] == seed_data["problem"].id


async def test_all_ac_autojudge_only(
    engine,
    session: AsyncSession,
    seed_data,
    monkeypatch,
    tmp_path,
):
    """All test cases AC on an autojudge_only contest → final_verdict=AC."""
    j = seed_data["judgment"]
    sub = seed_data["submission"]
    problem = seed_data["problem"]
    j_id = j.id
    fake_valkey = _FakeValkey()

    prob_dir = tmp_path / "contest" / problem.id
    prob_dir.mkdir(parents=True)
    (prob_dir / "001.in").write_bytes(b"1\n")
    (prob_dir / "001.out").write_bytes(b"1\n")
    (prob_dir / "002.in").write_bytes(b"2\n")
    (prob_dir / "002.out").write_bytes(b"2\n")

    monkeypatch.setattr("autojudge.submission_job.settings.PROBLEM_TESTCASE_DIR", str(tmp_path))

    async def _fake_compile(*args, **kwargs):
        return CompileResult(success=True, exit_code=0, compile_log="", artifact_data=b"artifact")

    async def _fake_run(*args, **kwargs):
        return RunResult(
            verdict=Verdict.AC,
            exit_code=0,
            wall_time_ms=10,
            memory_kb=128,
            stdout_excerpt=b"ok",
            stderr_excerpt=b"",
        )

    fake_pool = AsyncMock()
    fake_pool.acquire = AsyncMock(return_value="container-123")
    fake_pool.release = AsyncMock()

    monkeypatch.setattr("autojudge.submission_job.compile_submission", _fake_compile)
    monkeypatch.setattr("autojudge.submission_job.run_test_case", _fake_run)

    from autojudge.db import QueuedSubmission
    from autojudge.worker import _process_job

    qs = QueuedSubmission(
        judgment_id=j_id,
        submission_id=sub.id,
        contest_id=seed_data["contest"].id,
        contest_start_time=seed_data["contest"].start_time,
        problem_id=problem.id,
        team_id=sub.team_id,
        language_id=sub.language_id,
        source_code=sub.source_code,
        autojudge_only=True,
        accept_pe=False,
        stop_updating_scoreboard=120,
    )
    await session.close()

    async with open_db(engine) as db:
        await _process_job(
            submission=qs,
            db=db,
            valkey=fake_valkey,
            pool_manager=fake_pool,
            language_registry=default_language_registry(),
            docker_client=AsyncMock(),
            executor=None,
            worker_id="test-worker",
        )

    from sqlalchemy import select as sa_select

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        j_reloaded = await vs.get(SubmissionJudgment, j_id)
        assert j_reloaded.status == JudgmentStatus.DONE
        assert j_reloaded.autojudge_verdict == Verdict.AC
        assert j_reloaded.final_verdict == Verdict.AC

        results = (await vs.execute(sa_select(SubmissionTestResult))).scalars().all()
        assert len(results) == 2
        assert all(result.memory_kb == 128 for result in results)
        assert j_reloaded.max_memory_kb == 128
        assert j_reloaded.min_memory_kb == 128

    assert len(fake_valkey.published) == 1
    _, payload = fake_valkey.published[0]
    event_payload = json.loads(payload)
    assert event_payload["judge_time_ms"] >= 0
    assert event_payload == {
        "submission_id": sub.id,
        "judgment_id": j_id,
        "verdict": Verdict.AC.value,
        "contest_id": seed_data["contest"].id,
        "compile_log": None,
        "score": None,
        "judge_time_ms": event_payload["judge_time_ms"],
        "update_kind": "autojudge",
        "team_id": sub.team_id,
        "problem_id": problem.id,
    }


async def test_non_autojudge_only_finalizes_without_publishing_sse_event(
    engine,
    session: AsyncSession,
    seed_data,
    monkeypatch,
    tmp_path,
):
    """Worker should not publish SSE events when human review is still required."""
    j = seed_data["judgment"]
    sub = seed_data["submission"]
    problem = seed_data["problem"]
    j_id = j.id
    fake_valkey = _FakeValkey()

    prob_dir = tmp_path / "contest" / problem.id
    prob_dir.mkdir(parents=True)
    (prob_dir / "001.in").write_bytes(b"1\n")
    (prob_dir / "001.out").write_bytes(b"1\n")
    (prob_dir / "002.in").write_bytes(b"2\n")
    (prob_dir / "002.out").write_bytes(b"2\n")

    monkeypatch.setattr("autojudge.submission_job.settings.PROBLEM_TESTCASE_DIR", str(tmp_path))

    async def _fake_compile(*args, **kwargs):
        return CompileResult(success=True, exit_code=0, compile_log="", artifact_data=b"artifact")

    async def _fake_run(*args, **kwargs):
        return RunResult(
            verdict=Verdict.AC,
            exit_code=0,
            wall_time_ms=10,
            memory_kb=128,
            stdout_excerpt=b"ok",
            stderr_excerpt=b"",
        )

    fake_pool = AsyncMock()
    fake_pool.acquire = AsyncMock(return_value="container-123")
    fake_pool.release = AsyncMock()

    monkeypatch.setattr("autojudge.submission_job.compile_submission", _fake_compile)
    monkeypatch.setattr("autojudge.submission_job.run_test_case", _fake_run)

    from autojudge.db import QueuedSubmission
    from autojudge.worker import _process_job

    qs = QueuedSubmission(
        judgment_id=j_id,
        submission_id=sub.id,
        contest_id=seed_data["contest"].id,
        contest_start_time=seed_data["contest"].start_time,
        problem_id=problem.id,
        team_id=sub.team_id,
        language_id=sub.language_id,
        source_code=sub.source_code,
        autojudge_only=False,
        accept_pe=False,
        stop_updating_scoreboard=120,
    )
    await session.close()

    async with open_db(engine) as db:
        await _process_job(
            submission=qs,
            db=db,
            valkey=fake_valkey,
            pool_manager=fake_pool,
            language_registry=default_language_registry(),
            docker_client=AsyncMock(),
            executor=None,
            worker_id="test-worker",
        )

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        j_reloaded = await vs.get(SubmissionJudgment, j_id)
        assert j_reloaded.status == JudgmentStatus.DONE
        assert j_reloaded.autojudge_verdict == Verdict.AC
        assert j_reloaded.final_verdict is None

    assert fake_valkey.published == []


async def test_fail_fast_on_first_non_ac(
    engine,
    session: AsyncSession,
    seed_data,
    monkeypatch,
    tmp_path,
):
    """Worker stops after first non-AC verdict (fail-fast)."""
    j = seed_data["judgment"]
    sub = seed_data["submission"]
    problem = seed_data["problem"]
    j_id = j.id
    fake_valkey = _FakeValkey()

    prob_dir = tmp_path / "contest" / problem.id
    prob_dir.mkdir(parents=True)
    (prob_dir / "001.in").write_bytes(b"1\n")
    (prob_dir / "001.out").write_bytes(b"1\n")
    (prob_dir / "002.in").write_bytes(b"2\n")
    (prob_dir / "002.out").write_bytes(b"2\n")

    monkeypatch.setattr("autojudge.submission_job.settings.PROBLEM_TESTCASE_DIR", str(tmp_path))

    async def _fake_compile(*args, **kwargs):
        return CompileResult(success=True, exit_code=0, compile_log="", artifact_data=b"bin")

    call_count = 0

    async def _fake_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return RunResult(verdict=Verdict.AC, exit_code=0, wall_time_ms=5, stdout_excerpt=b"", stderr_excerpt=b"")
        return RunResult(
            verdict=Verdict.WA,
            exit_code=0,
            wall_time_ms=10,
            memory_kb=64,
            stdout_excerpt=b"wrong",
            stderr_excerpt=b"",
        )

    fake_pool = AsyncMock()
    fake_pool.acquire = AsyncMock(return_value="container-456")
    fake_pool.release = AsyncMock()

    monkeypatch.setattr("autojudge.submission_job.compile_submission", _fake_compile)
    monkeypatch.setattr("autojudge.submission_job.run_test_case", _fake_run)

    from autojudge.db import QueuedSubmission
    from autojudge.worker import _process_job

    qs = QueuedSubmission(
        judgment_id=j_id,
        submission_id=sub.id,
        contest_id=seed_data["contest"].id,
        contest_start_time=seed_data["contest"].start_time,
        problem_id=problem.id,
        team_id=sub.team_id,
        language_id=sub.language_id,
        source_code=sub.source_code,
        autojudge_only=True,
        accept_pe=False,
        stop_updating_scoreboard=120,
    )
    await session.close()

    async with open_db(engine) as db:
        await _process_job(
            submission=qs,
            db=db,
            valkey=fake_valkey,
            pool_manager=fake_pool,
            language_registry=default_language_registry(),
            docker_client=AsyncMock(),
            executor=None,
            worker_id="test-worker",
        )

    from sqlalchemy import select as sa_select

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        j_reloaded = await vs.get(SubmissionJudgment, j_id)
        assert j_reloaded.autojudge_verdict == Verdict.WA

        results = (await vs.execute(sa_select(SubmissionTestResult))).scalars().all()
        assert len(results) == 2
        assert call_count == 2
