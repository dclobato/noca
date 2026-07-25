#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Tests for autojudge.worker — failure, retry, and recovery paths.

Covers the recoverable-isolate retry, cancellation preserving inflight metadata
for the reaper, a missing test-case file, the idempotency lock, and container
pool exhaustion. Verdict outcomes live in ``test_autojudge_worker.py``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from _autojudge_worker_fakes import (
    _FakeValkey,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autojudge.db import open_db
from autojudge.runner import CompileResult, IsolateError, RunResult
from shared.enumerations import JudgmentStatus, Verdict
from shared.language_registry import default_language_registry
from web.models.submission import (
    SubmissionJudgment,
    SubmissionTestResult,
)


async def test_retries_once_after_recoverable_isolate_error(
    engine,
    session: AsyncSession,
    seed_data,
    monkeypatch,
    tmp_path,
):
    """A missing isolate cgroup file should recycle the container and retry once."""
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
            raise IsolateError("Cannot open /sys/fs/cgroup/box-0/cpu.stat: No such file or directory")
        return RunResult(
            verdict=Verdict.AC,
            exit_code=0,
            wall_time_ms=10,
            memory_kb=64,
            stdout_excerpt=b"ok",
            stderr_excerpt=b"",
        )

    fake_pool = AsyncMock()
    fake_pool.acquire = AsyncMock(side_effect=["container-bad", "container-good"])
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

        results = (await vs.execute(sa_select(SubmissionTestResult))).scalars().all()
        assert len(results) == 2

    assert call_count == 3
    assert fake_pool.acquire.await_count == 2
    assert fake_pool.release.await_count == 2
    assert fake_pool.release.await_args_list[0].args == ("container-bad",)
    assert fake_pool.release.await_args_list[1].args == ("container-good",)


async def test_cancelled_job_preserves_inflight_metadata_for_reaper(
    engine,
    session: AsyncSession,
    seed_data,
    monkeypatch,
):
    """Cancellation must not delete inflight/hash state before the reaper can recover the job."""
    j = seed_data["judgment"]
    sub = seed_data["submission"]
    j_id = j.id
    fake_valkey = _FakeValkey()

    job_key = f"judge:job:{j_id}"
    lock_key = f"judge:lock:{j_id}"
    inflight_key = "judge:queue:inflight"
    inflight_times_key = "judge:queue:inflight:times"
    fake_valkey.data[job_key] = {"judgment_id": j_id, "job_kind": "submission", "requeue_count": "0"}
    fake_valkey.data[inflight_key] = [j_id]
    fake_valkey.data[inflight_times_key] = {j_id: 123.0}

    async def _fake_compile(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr("autojudge.submission_job.compile_submission", _fake_compile)

    from autojudge.db import QueuedSubmission
    from autojudge.worker import _process_job

    qs = QueuedSubmission(
        judgment_id=j_id,
        submission_id=sub.id,
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
        with pytest.raises(asyncio.CancelledError):
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

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        j_reloaded = await vs.get(SubmissionJudgment, j_id)
        assert j_reloaded.status == JudgmentStatus.DISPATCHED

    assert lock_key in fake_valkey.data
    assert job_key in fake_valkey.data
    assert fake_valkey.data[inflight_key] == [j_id]
    assert fake_valkey.data[inflight_times_key][j_id] == 123.0


async def test_missing_testcase_file_sets_failed(
    engine,
    session: AsyncSession,
    seed_data,
    monkeypatch,
):
    """If test case files don't exist on disk, judgment should be FAILED."""
    j = seed_data["judgment"]
    sub = seed_data["submission"]
    j_id = j.id
    fake_valkey = _FakeValkey()

    async def _fake_compile(*args, **kwargs):
        return CompileResult(success=True, exit_code=0, compile_log="", artifact_data=b"bin")

    monkeypatch.setattr("autojudge.submission_job.compile_submission", _fake_compile)
    monkeypatch.setattr(
        "autojudge.submission_job._load_test_cases",
        lambda pid: (_ for _ in ()).throw(FileNotFoundError(f"No dir for {pid}")),
    )

    from autojudge.db import QueuedSubmission
    from autojudge.worker import _process_job

    qs = QueuedSubmission(
        judgment_id=j_id,
        submission_id=sub.id,
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

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        j_reloaded = await vs.get(SubmissionJudgment, j_id)
        assert j_reloaded.status == JudgmentStatus.FAILED
        assert "No dir for" in j_reloaded.error_message


async def test_idempotency_lock_prevents_duplicate(
    engine,
    session: AsyncSession,
    seed_data,
    monkeypatch,
):
    """If the lock is already held, _process_job should return early."""
    j = seed_data["judgment"]
    sub = seed_data["submission"]
    j_id = j.id

    fake_valkey = _FakeValkey()
    lock_key = f"judge:lock:{j_id}"
    fake_valkey.data[lock_key] = "other-worker"

    from autojudge.db import QueuedSubmission
    from autojudge.worker import _process_job

    qs = QueuedSubmission(
        judgment_id=j_id,
        submission_id=sub.id,
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

    # Judgment should still be QUEUED — nothing was processed
    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        j_reloaded = await vs.get(SubmissionJudgment, j_id)
        assert j_reloaded.status == JudgmentStatus.QUEUED


async def test_pool_exhausted_sets_failed(
    engine,
    session: AsyncSession,
    seed_data,
    monkeypatch,
    tmp_path,
):
    """PoolExhaustedError should result in FAILED status."""
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

    monkeypatch.setattr("autojudge.submission_job.compile_submission", _fake_compile)

    from autojudge.pool import PoolExhaustedError

    fake_pool = AsyncMock()
    fake_pool.acquire = AsyncMock(side_effect=PoolExhaustedError("python3"))

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

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        j_reloaded = await vs.get(SubmissionJudgment, j_id)
        assert j_reloaded.status == JudgmentStatus.FAILED
        assert "python3" in j_reloaded.error_message
