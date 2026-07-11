"""
Tests for autojudge.worker — the main judge pipeline.

These tests monkeypatch compile/run phases and container management so they
run without Docker.  Database operations use the real in-memory SQLite engine
(via conftest fixtures).  Redis/Valkey is replaced by a lightweight fake.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import docker.errors
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autojudge.db import RecoverableProfilingJob, RecoverableSubmissionJob, open_db
from autojudge.runner import CompileResult, IsolateError, RunResult
from autojudge.types import ArenaQueuedTestCase, QueuedArenaSubmission, RepetitionCaseResult
from shared.enumerations import JudgmentStatus, ProfilingStatus, Verdict
from shared.language_registry import default_language_registry
from shared.services.valkey_service.worker_commands import LivePauseFlag
from shared.services.worker_pause_state import bump_worker_pause_state
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem, ProblemTestCase
from web.models.submission import (
    Submission,
    SubmissionJudgment,
    SubmissionTestResult,
)
from web.models.users import User

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uid() -> str:
    return str(uuid.uuid4())


def _jobs_completed_value(job_kind: str, outcome: str) -> float:
    """Read the current jobs_completed counter value for one label pair."""
    from autojudge.metrics import JOBS_COMPLETED_TOTAL

    return JOBS_COMPLETED_TOTAL.labels(job_kind=job_kind, outcome=outcome)._value.get()


def _make_language(session: AsyncSession) -> Language:
    lang = Language(
        id="python3",
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


def _make_submission(
    session: AsyncSession,
    problem: Problem,
    team: User,
    language: Language,
) -> Submission:
    src = "print('hello')"
    sub = Submission(
        problem_id=problem.id,
        team_id=team.id,
        language_id=language.id,
        source_code=src,
        source_hash=hashlib.sha256(src.encode()).hexdigest(),
        source_size_bytes=len(src.encode()),
    )
    session.add(sub)
    return sub


def _make_judgment(
    session: AsyncSession,
    submission: Submission,
    status: JudgmentStatus = JudgmentStatus.QUEUED,
) -> SubmissionJudgment:
    j = SubmissionJudgment(submission_id=submission.id, status=status)
    session.add(j)
    return j


@pytest.mark.asyncio
async def test_worker_loop_suppresses_dequeue_until_resumed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Worker slots do not dequeue while paused and resume when the flag clears."""
    from autojudge import worker as worker_module

    shutdown_event = asyncio.Event()
    pause_flag = LivePauseFlag(paused=True)
    sleep_delays: list[float] = []
    dequeue_calls = 0

    async def _fake_sleep(delay: float) -> None:
        sleep_delays.append(delay)
        if pause_flag.paused:
            pause_flag.paused = False

    async def _fake_dequeue(_valkey: object) -> None:
        nonlocal dequeue_calls
        dequeue_calls += 1
        shutdown_event.set()
        return None

    monkeypatch.setattr(worker_module.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(worker_module, "dequeue_job_id", _fake_dequeue)

    await worker_module._worker_loop(
        slot=0,
        shutdown_event=shutdown_event,
        db_engine=object(),
        valkey=object(),  # type: ignore[arg-type]
        pool_manager=object(),  # type: ignore[arg-type]
        language_registry={},
        docker_client=object(),  # type: ignore[arg-type]
        executor=object(),  # type: ignore[arg-type]
        wid="judge-pause-test",
        pause_flag=pause_flag,
    )

    assert sleep_delays[0] == 1
    assert dequeue_calls == 1


@pytest.mark.asyncio
async def test_autojudge_startup_restores_paused_state(engine: object) -> None:
    """The autojudge startup helper adopts the committed PostgreSQL pause state."""
    from autojudge import worker as worker_module

    worker_id_val = "judge-startup-paused"
    async with engine.begin() as conn:  # type: ignore[attr-defined]
        await bump_worker_pause_state(
            conn,
            worker_class="autojudge",
            worker_id=worker_id_val,
            paused=True,
            paused_by="admin@example.com",
        )

    pause_flag = LivePauseFlag()
    await worker_module._restore_startup_pause_state(
        engine,
        worker_id_val,
        pause_flag,
    )

    assert pause_flag.paused is True
    assert pause_flag.paused_by == "admin@example.com"
    assert pause_flag.applied_generation == 1


# ---------------------------------------------------------------------------
# Fake Valkey for unit tests
# ---------------------------------------------------------------------------


class _FakePipeline:
    """Fake pipeline that records commands for _FakeValkey."""

    def __init__(self, parent: _FakeValkey) -> None:
        self.parent = parent
        self.commands: list[tuple[str, Any, Any]] = []

    def lrem(self, name: str, count: int, value: str) -> None:
        self.commands.append(("lrem", name, (count, value)))

    def zrem(self, name: str, *values: str) -> None:
        self.commands.append(("zrem", name, values))

    def delete(self, *keys: str) -> None:
        self.commands.append(("delete", keys, None))

    async def execute(self) -> None:
        for op, key, payload in self.commands:
            if op == "lrem":
                count, value = payload
                items = self.parent.data.get(key, [])
                if isinstance(items, list):
                    removed = 0
                    kept = []
                    for item in items:
                        if item == value and (count == 0 or removed < count):
                            removed += 1
                            continue
                        kept.append(item)
                    self.parent.data[key] = kept
            elif op == "zrem":
                zset = self.parent.data.get(key, {})
                if isinstance(zset, dict):
                    for value in payload:
                        zset.pop(value, None)
            elif op == "delete":
                for item in key:
                    self.parent.data.pop(item, None)


class _FakeValkey:
    """Minimal Valkey replacement that records calls and supports lock checks."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.published: list[tuple[str, Any]] = []

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    async def set(self, key: str, value: Any, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def zadd(self, name: str, mapping: dict[str, float]) -> int:
        zset = self.data.setdefault(name, {})
        if not isinstance(zset, dict):
            zset = {}
            self.data[name] = zset
        for key, value in mapping.items():
            zset[key] = value
        return len(mapping)

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self.data:
                del self.data[k]
                count += 1
        return count

    async def lrem(self, name: str, count: int, value: str) -> int:
        items = self.data.get(name, [])
        if not isinstance(items, list):
            return 0
        removed = 0
        kept = []
        for item in items:
            if item == value and (count == 0 or removed < count):
                removed += 1
                continue
            kept.append(item)
        self.data[name] = kept
        return removed

    async def zrem(self, name: str, *values: str) -> int:
        zset = self.data.get(name, {})
        if not isinstance(zset, dict):
            return 0
        removed = 0
        for value in values:
            if value in zset:
                del zset[value]
                removed += 1
        return removed

    async def lrange(self, name: str, start: int, end: int) -> list[Any]:
        items = self.data.get(name, [])
        if not isinstance(items, list):
            return []
        if end == -1:
            end = len(items) - 1
        return items[start : end + 1]

    async def hgetall(self, name: str) -> dict[str, Any]:
        value = self.data.get(name, {})
        return value if isinstance(value, dict) else {}

    async def hget(self, name: str, key: str) -> Any:
        value = self.data.get(name, {})
        if not isinstance(value, dict):
            return None
        return value.get(key)

    async def hset(
        self,
        name: str,
        key: str | None = None,
        value: Any | None = None,
        *,
        mapping: dict[str, Any] | None = None,
    ) -> int:
        bucket = self.data.setdefault(name, {})
        if not isinstance(bucket, dict):
            bucket = {}
            self.data[name] = bucket
        if mapping is not None:
            bucket.update(mapping)
            return len(mapping)
        if key is None:
            return 0
        bucket[key] = value
        return 1

    async def lpush(self, name: str, *values: Any) -> int:
        items = self.data.setdefault(name, [])
        if not isinstance(items, list):
            items = []
            self.data[name] = items
        for value in values:
            items.insert(0, value)
        return len(items)

    async def rpush(self, name: str, *values: Any) -> int:
        items = self.data.setdefault(name, [])
        if not isinstance(items, list):
            items = []
            self.data[name] = items
        items.extend(values)
        return len(items)

    async def publish(self, channel: str, message: Any) -> int:
        self.published.append((channel, message))
        return 1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed_data(
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
    team_user: User,
):
    """Seed a language, submission, judgment, and 2 test cases."""
    lang = _make_language(session)
    await session.flush()
    sub = _make_submission(session, contest_problem, team_user, lang)
    await session.flush()
    j = _make_judgment(session, sub)
    await session.flush()

    tc1 = ProblemTestCase(problem_id=contest_problem.id, ordinal=1)
    tc2 = ProblemTestCase(problem_id=contest_problem.id, ordinal=2)
    session.add_all([tc1, tc2])
    await session.flush()
    await session.commit()

    return {
        "language": lang,
        "submission": sub,
        "judgment": j,
        "test_cases": [tc1, tc2],
        "contest": running_contest,
        "problem": contest_problem,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


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
    from shared.language_registry import default_language_registry

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
    from shared.language_registry import default_language_registry

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
    from shared.language_registry import default_language_registry

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
    from shared.language_registry import default_language_registry

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
    from shared.language_registry import default_language_registry

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
    from shared.language_registry import default_language_registry

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


async def test_startup_reconciliation_requeues_orphaned_jobs():
    """Startup reconciliation should rebuild queue state from non-terminal DB jobs."""
    from autojudge.db import QueuedProfilingRun, QueuedSubmission
    from autojudge.worker import _reconcile_queue_state

    fake_valkey = _FakeValkey()
    fake_valkey.data["judge:queue:inflight"] = ["judgment-orphan", "profiling-orphan"]
    fake_valkey.data["judge:queue:priority"] = ["judgment-healthy"]
    fake_valkey.data["judge:queue:inflight:times"] = {
        "judgment-orphan": 10.0,
        "profiling-orphan": 11.0,
    }
    fake_valkey.data["judge:lock:judgment-orphan"] = "old-worker"
    fake_valkey.data["judge:lock:profiling-orphan"] = "old-worker"
    fake_valkey.data["judge:job:judgment-orphan"] = {
        "judgment_id": "judgment-orphan",
        "contest_id": "contest-1",
        "submission_id": "submission-1",
        "is_rejudge": "false",
        "requeue_count": "2",
        "job_kind": "submission",
    }

    class _FakeDb:
        async def list_recoverable_submission_jobs(self):
            return [
                RecoverableSubmissionJob(
                    status=JudgmentStatus.JUDGING,
                    payload=QueuedSubmission(
                        judgment_id="judgment-orphan",
                        submission_id="submission-1",
                        contest_id="contest-1",
                        contest_start_time=datetime.now(UTC),
                        problem_id="problem-1",
                        team_id="team-1",
                        language_id="gcc-c17",
                        source_code="int main(){}",
                        autojudge_only=True,
                        accept_pe=False,
                        stop_updating_scoreboard=120,
                    ),
                ),
                RecoverableSubmissionJob(
                    status=JudgmentStatus.QUEUED,
                    payload=QueuedSubmission(
                        judgment_id="judgment-healthy",
                        submission_id="submission-2",
                        contest_id="contest-1",
                        contest_start_time=datetime.now(UTC),
                        problem_id="problem-2",
                        team_id="team-2",
                        language_id="python3",
                        source_code="print(1)",
                        autojudge_only=True,
                        accept_pe=False,
                        stop_updating_scoreboard=120,
                    ),
                ),
            ]

        async def list_recoverable_profiling_jobs(self):
            return [
                RecoverableProfilingJob(
                    status=ProfilingStatus.RUNNING,
                    payload=QueuedProfilingRun(
                        profiling_run_id="profiling-orphan",
                        contest_id="contest-1",
                        problem_id="problem-1",
                        language_id="gcc-c17",
                        source_code="int main(){}",
                        safety_factor=2.0,
                    ),
                )
            ]

        async def list_recoverable_arena_submission_jobs(self):
            return []

    await _reconcile_queue_state(_FakeDb(), fake_valkey)  # type: ignore[arg-type]

    assert fake_valkey.data["judge:queue:pending"] == ["judgment-orphan", "judgment-healthy"]
    assert fake_valkey.data["judge:queue:profiling"] == ["profiling-orphan"]
    assert fake_valkey.data["judge:queue:priority"] == ["judgment-healthy"]
    assert fake_valkey.data["judge:queue:inflight"] == []
    assert fake_valkey.data["judge:queue:inflight:times"] == {}
    assert "judge:lock:judgment-orphan" not in fake_valkey.data
    assert "judge:lock:profiling-orphan" not in fake_valkey.data
    assert fake_valkey.data["judge:job:judgment-orphan"]["requeue_count"] == "2"
    assert fake_valkey.data["judge:job:judgment-healthy"]["judgment_id"] == "judgment-healthy"
    assert fake_valkey.data["judge:job:profiling-orphan"]["contest_id"] == "contest-1"


async def test_reconcile_loop_runs_periodically_and_stops_on_shutdown(monkeypatch):
    """The periodic loop reconciles with phase='Periodic' and exits on shutdown."""
    from autojudge import reconcile as reconcile_module
    from autojudge import worker as worker_module

    monkeypatch.setattr(reconcile_module.settings, "RECONCILER_INTERVAL_S", 0.0)

    shutdown_event = asyncio.Event()
    calls: list[str] = []

    @asynccontextmanager
    async def _fake_open_db(_engine):
        yield object()

    async def _fake_reconcile(_db, _valkey, *, phase="Startup"):
        calls.append(phase)
        # Stop after the first periodic pass so the loop terminates.
        shutdown_event.set()

    monkeypatch.setattr(reconcile_module, "open_db", _fake_open_db)
    monkeypatch.setattr(reconcile_module, "reconcile_queue_state", _fake_reconcile)

    await asyncio.wait_for(
        worker_module._reconcile_loop(shutdown_event, object(), _FakeValkey()),  # type: ignore[arg-type]
        timeout=5.0,
    )

    assert calls == ["Periodic"]


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
    from shared.language_registry import default_language_registry

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
    from shared.language_registry import default_language_registry

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


async def test_dispatch_job_increments_completed_lock_miss_metric() -> None:
    """_dispatch_job should count lock misses in jobs_completed_total."""
    from autojudge import worker as worker_module
    from shared.queue_schema import JobKind

    fake_valkey = _FakeValkey()
    job_id = _uid()
    fake_valkey.data[f"judge:lock:{job_id}"] = "held-by-other-worker"
    before = _jobs_completed_value(JobKind.SUBMISSION, "lock_miss")

    await worker_module._dispatch_job(
        job_id=job_id,
        job_kind=JobKind.SUBMISSION,
        db=AsyncMock(),
        valkey=fake_valkey,
        pool_manager=AsyncMock(),
        language_registry={},
        docker_client=AsyncMock(),
        executor=AsyncMock(),
        wid="worker-a",
    )

    after = _jobs_completed_value(JobKind.SUBMISSION, "lock_miss")
    assert after - before == 1.0


async def test_dispatch_job_increments_completed_done_metric_for_submission(monkeypatch) -> None:
    """Successful submission dispatch should increment jobs_completed_total{outcome='done'}."""
    from autojudge import dispatch as dispatch_module
    from autojudge import worker as worker_module
    from shared.queue_schema import JobKind

    fake_valkey = _FakeValkey()
    job_id = _uid()
    fake_submission = AsyncMock()
    fake_db = AsyncMock()
    fake_db.get_submission_for_judging = AsyncMock(return_value=fake_submission)
    monkeypatch.setattr(dispatch_module, "process_submission_job", AsyncMock(return_value=None))

    before = _jobs_completed_value(JobKind.SUBMISSION, "done")

    await worker_module._dispatch_job(
        job_id=job_id,
        job_kind=JobKind.SUBMISSION,
        db=fake_db,
        valkey=fake_valkey,
        pool_manager=AsyncMock(),
        language_registry={},
        docker_client=AsyncMock(),
        executor=AsyncMock(),
        wid="worker-a",
    )

    after = _jobs_completed_value(JobKind.SUBMISSION, "done")
    assert after - before == 1.0


async def test_dispatch_job_routes_arena_submission(monkeypatch) -> None:
    """Arena submission jobs should use the Arena adapter and done metric."""
    from autojudge import dispatch as dispatch_module
    from autojudge import worker as worker_module
    from shared.queue_schema import JobKind

    fake_valkey = _FakeValkey()
    job_id = _uid()
    fake_submission = AsyncMock()
    fake_db = AsyncMock()
    fake_db.get_arena_submission_for_judging = AsyncMock(return_value=fake_submission)
    process_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(dispatch_module, "process_arena_submission_job", process_mock)

    before = _jobs_completed_value(JobKind.ARENA_SUBMISSION, "done")

    await worker_module._dispatch_job(
        job_id=job_id,
        job_kind=JobKind.ARENA_SUBMISSION,
        db=fake_db,
        valkey=fake_valkey,
        pool_manager=AsyncMock(),
        language_registry={},
        docker_client=AsyncMock(),
        executor=AsyncMock(),
        wid="worker-a",
    )

    after = _jobs_completed_value(JobKind.ARENA_SUBMISSION, "done")
    assert after - before == 1.0
    fake_db.get_submission_for_judging.assert_not_called()
    process_mock.assert_awaited_once()


async def test_dispatch_job_falls_back_to_arena_when_submission_lookup_missing(monkeypatch) -> None:
    """Legacy jobs missing job_kind metadata should still route to Arena when present."""
    from autojudge import dispatch as dispatch_module
    from autojudge import worker as worker_module
    from shared.queue_schema import JobKind

    fake_valkey = _FakeValkey()
    job_id = _uid()
    fake_submission = AsyncMock()
    fake_db = AsyncMock()
    fake_db.get_submission_for_judging = AsyncMock(side_effect=LookupError("missing web submission"))
    fake_db.get_arena_submission_for_judging = AsyncMock(return_value=fake_submission)
    process_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(dispatch_module, "process_arena_submission_job", process_mock)

    before = _jobs_completed_value(JobKind.ARENA_SUBMISSION, "done")

    await worker_module._dispatch_job(
        job_id=job_id,
        job_kind=JobKind.SUBMISSION,
        db=fake_db,
        valkey=fake_valkey,
        pool_manager=AsyncMock(),
        language_registry={},
        docker_client=AsyncMock(),
        executor=AsyncMock(),
        wid="worker-a",
    )

    after = _jobs_completed_value(JobKind.ARENA_SUBMISSION, "done")
    assert after - before == 1.0
    fake_db.get_submission_for_judging.assert_awaited_once()
    fake_db.get_arena_submission_for_judging.assert_awaited_once()
    process_mock.assert_awaited_once()


async def test_process_arena_submission_records_only_first_non_ac(monkeypatch) -> None:
    """Arena adapter should persist only the first non-AC test result."""
    from autojudge import arena_submission_job as arena_job
    from autojudge.types import ProblemLimits

    submission = QueuedArenaSubmission(
        judgment_id="arena-judgment-1",
        submission_id="arena-submission-1",
        user_id="arena-user-1",
        problem_id="arena-problem-1",
        problem_number=1,
        problem_title="Arena Problem",
        language_id="python3",
        source_code="print('bad')",
        limits=ProblemLimits(time_limit_ms=1000, memory_limit_kb=65536, pids_limit=16),
        test_cases=(
            ArenaQueuedTestCase("case-1", 1, b"1\n", b"1\n"),
            ArenaQueuedTestCase("case-2", 2, b"2\n", b"2\n"),
        ),
    )
    fake_db = AsyncMock()
    fake_pool = AsyncMock()
    fake_pool.acquire = AsyncMock(return_value="container-1")
    fake_pool.release = AsyncMock(return_value=None)
    run_results = [
        RepetitionCaseResult(
            verdict=Verdict.AC,
            total_wall_time_ms=10,
            peak_memory_kb=100,
            peak_output_bytes=2,
            peak_pids=1,
            exit_code=0,
            exit_signal=None,
            stdout_excerpt=b"1\n",
            stderr_excerpt=b"",
        ),
        RepetitionCaseResult(
            verdict=Verdict.WA,
            total_wall_time_ms=12,
            peak_memory_kb=120,
            peak_output_bytes=6,
            peak_pids=1,
            exit_code=0,
            exit_signal=None,
            stdout_excerpt=b"wrong\n",
            stderr_excerpt=b"",
        ),
    ]
    monkeypatch.setattr(
        arena_job,
        "compile_submission",
        AsyncMock(return_value=CompileResult(success=True, exit_code=0, compile_log="", artifact_data=b"artifact")),
    )
    monkeypatch.setattr(arena_job, "_run_repeated_test_case", AsyncMock(side_effect=run_results))

    await arena_job.process_arena_submission_job(
        submission=submission,
        db=fake_db,
        valkey=_FakeValkey(),
        pool_manager=fake_pool,
        language_registry=default_language_registry(),
        docker_client=AsyncMock(),
        executor=AsyncMock(),
        worker_id="worker-a",
    )

    fake_db.insert_arena_test_result.assert_awaited_once()
    kwargs = fake_db.insert_arena_test_result.await_args.kwargs
    assert kwargs["test_case_id"] == "case-2"
    assert kwargs["verdict"] == Verdict.WA
    fake_db.set_arena_judgment_done.assert_awaited_once()


async def test_dispatch_job_increments_completed_lookup_error_metric() -> None:
    """LookupError during dispatch should increment jobs_completed_total{outcome='lookup_error'}."""
    from autojudge import worker as worker_module
    from shared.queue_schema import JobKind

    fake_valkey = _FakeValkey()
    job_id = _uid()
    fake_db = AsyncMock()
    fake_db.get_submission_for_judging = AsyncMock(side_effect=LookupError("missing job"))
    fake_db.get_arena_submission_for_judging = AsyncMock(side_effect=LookupError("missing arena job"))

    before = _jobs_completed_value(JobKind.SUBMISSION, "lookup_error")

    await worker_module._dispatch_job(
        job_id=job_id,
        job_kind=JobKind.SUBMISSION,
        db=fake_db,
        valkey=fake_valkey,
        pool_manager=AsyncMock(),
        language_registry={},
        docker_client=AsyncMock(),
        executor=AsyncMock(),
        wid="worker-a",
    )

    after = _jobs_completed_value(JobKind.SUBMISSION, "lookup_error")
    assert after - before == 1.0


async def test_dispatch_job_increments_completed_failed_metric_on_submission_exception(
    monkeypatch,
) -> None:
    """Unhandled submission exceptions should increment jobs_completed_total{outcome='failed'}."""
    from autojudge import dispatch as dispatch_module
    from autojudge import worker as worker_module
    from shared.queue_schema import JobKind

    fake_valkey = _FakeValkey()
    job_id = _uid()

    class _FakeSubmission:
        contest_start_time = datetime.now(UTC)

    fake_db = AsyncMock()
    fake_db.get_submission_for_judging = AsyncMock(return_value=_FakeSubmission())
    fake_db.set_judgment_failed = AsyncMock(return_value=None)
    monkeypatch.setattr(
        dispatch_module,
        "process_submission_job",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    before = _jobs_completed_value(JobKind.SUBMISSION, "failed")

    await worker_module._dispatch_job(
        job_id=job_id,
        job_kind=JobKind.SUBMISSION,
        db=fake_db,
        valkey=fake_valkey,
        pool_manager=AsyncMock(),
        language_registry={},
        docker_client=AsyncMock(),
        executor=AsyncMock(),
        wid="worker-a",
    )

    after = _jobs_completed_value(JobKind.SUBMISSION, "failed")
    assert after - before == 1.0


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
    from shared.language_registry import default_language_registry

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


async def test_required_image_preflight_lists_missing_images():
    """Startup preflight should list all missing image refs once, in sorted order."""
    from autojudge.image_sync import assert_required_images_present
    from shared.language_registry import default_language_registry

    registry = default_language_registry()

    class _FakeImages:
        def get(self, image_ref: str):
            if image_ref in {
                "noca/judge-java:compile",
                "noca/judge-python3:run",
            }:
                raise docker.errors.ImageNotFound("missing")
            return object()

    class _FakeDockerClient:
        images = _FakeImages()

    with pytest.raises(RuntimeError) as excinfo:
        await assert_required_images_present(
            docker_client=_FakeDockerClient(),  # type: ignore[arg-type]
            executor=None,  # type: ignore[arg-type]
            language_registry=registry,
        )

    assert str(excinfo.value) == (
        "Missing required judge images. Worker startup aborted.\n- noca/judge-java:compile\n- noca/judge-python3:run"
    )


async def test_required_image_preflight_propagates_non_missing_docker_error():
    """Non-ImageNotFound Docker failures should propagate unchanged."""
    from autojudge.image_sync import assert_required_images_present
    from shared.language_registry import default_language_registry

    class _FakeImages:
        def get(self, image_ref: str):
            if image_ref == "noca/judge-java:compile":
                raise docker.errors.APIError("daemon unavailable")
            return object()

    class _FakeDockerClient:
        images = _FakeImages()

    with pytest.raises(docker.errors.APIError):
        await assert_required_images_present(
            docker_client=_FakeDockerClient(),  # type: ignore[arg-type]
            executor=None,  # type: ignore[arg-type]
            language_registry=default_language_registry(),
        )


async def test_run_worker_fails_fast_before_pool_warm_and_cleans_up(monkeypatch):
    """run_worker should abort before PoolManager warmup when required images are missing."""
    from autojudge import worker as worker_module

    missing_image = "noca/judge-java:compile"
    pool_manager_created = False
    valkey_opened = False

    class _FakeDockerClient:
        def __init__(self):
            self.closed = False

        def close(self) -> None:
            self.closed = True

    fake_docker_client = _FakeDockerClient()

    class _FakeEngine:
        def __init__(self) -> None:
            self.disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    fake_engine = _FakeEngine()

    class _FakeDb:
        async def list_languages(self) -> list[dict[str, object]]:
            return [
                {
                    "id": "python3",
                    "name": "Python 3.14",
                    "compile_image": "noca/judge-python3:compile",
                    "run_image": "noca/judge-python3:run",
                    "compile_cmd": ["python3", "-m", "py_compile", "/sandbox/source.py"],
                    "run_cmd": ["python3", "-u", "/sandbox/source.py"],
                    "source_filename": "source.py",
                    "artifact_path": "/sandbox/source.py",
                    "artifact_is_source": True,
                    "compile_timeout_s": 10.0,
                }
            ]

    @asynccontextmanager
    async def _fake_open_db(engine):
        assert engine is fake_engine
        yield _FakeDb()

    def _fake_pool_manager(*args, **kwargs):
        nonlocal pool_manager_created
        pool_manager_created = True
        raise AssertionError("PoolManager should not be constructed when startup preflight fails")

    async def _fake_from_url(*args, **kwargs):
        nonlocal valkey_opened
        valkey_opened = True
        raise AssertionError("Valkey should not be opened before image preflight passes")

    monkeypatch.setattr(worker_module, "wait_for_db", AsyncMock())
    monkeypatch.setattr(worker_module, "wait_for_valkey", AsyncMock())
    monkeypatch.setattr(worker_module.docker, "DockerClient", lambda *args, **kwargs: fake_docker_client)
    monkeypatch.setattr(worker_module, "create_worker_engine", lambda: fake_engine)
    monkeypatch.setattr(worker_module, "open_db", _fake_open_db)
    monkeypatch.setattr(
        worker_module,
        "assert_required_images_present",
        AsyncMock(
            side_effect=RuntimeError(f"Missing required judge images. Worker startup aborted.\n- {missing_image}")
        ),
    )
    monkeypatch.setattr(worker_module, "PoolManager", _fake_pool_manager)
    monkeypatch.setattr(worker_module.aiovalkey, "from_url", _fake_from_url)
    monkeypatch.setattr(worker_module, "touch_heartbeat_file", lambda: None)
    monkeypatch.setattr(worker_module, "remove_heartbeat_file", lambda: None)
    monkeypatch.setattr(worker_module.settings, "LOCK_TTL_SECONDS", 120)
    monkeypatch.setattr(worker_module.settings, "REAPER_STALE_THRESHOLD_MINUTES", 1)
    monkeypatch.setattr(worker_module, "registry_from_rows", lambda rows: {})

    with pytest.raises(RuntimeError) as excinfo:
        await worker_module.run_worker()

    assert str(excinfo.value) == (f"Missing required judge images. Worker startup aborted.\n- {missing_image}")
    assert fake_engine.disposed is True
    assert fake_docker_client.closed is True
    assert pool_manager_created is False
    assert valkey_opened is False


@pytest.mark.asyncio
async def test_load_language_registry_when_ready_retries_until_language_exists(monkeypatch):
    """Worker startup should wait until an active language is available."""
    from autojudge import worker as worker_module

    class _FakeDb:
        def __init__(self) -> None:
            self.calls = 0

        async def list_languages(self) -> list[dict[str, object]]:
            self.calls += 1
            if self.calls == 1:
                return []
            return [
                {
                    "id": "python3",
                    "name": "Python 3.14",
                    "icon": "python",
                    "compile_image": "noca/judge-python3:compile",
                    "run_image": "noca/judge-python3:run",
                    "compile_cmd": ["python3", "-m", "py_compile", "/sandbox/source.py"],
                    "run_cmd": ["python3", "-u", "/sandbox/source.py"],
                    "source_filename": "source.py",
                    "artifact_path": "/sandbox/source.py",
                    "artifact_is_source": True,
                    "compile_timeout_s": 10.0,
                }
            ]

    fake_db = _FakeDb()
    sleep_calls: list[float] = []

    async def _fake_wait_for(awaitable, timeout: float):
        sleep_calls.append(timeout)
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(worker_module.asyncio, "wait_for", _fake_wait_for)

    registry = await worker_module._load_language_registry_when_ready(
        fake_db,  # type: ignore[arg-type]
        asyncio.Event(),
    )

    assert fake_db.calls == 2
    assert sleep_calls == [worker_module._LANGUAGE_REGISTRY_RETRY_INTERVAL_S]
    assert sorted(registry) == ["python3"]


@pytest.mark.asyncio
async def test_sync_registry_images_from_settings_disabled_is_noop(monkeypatch):
    """Image sync should leave the registry unchanged when no registry is configured."""
    from autojudge import worker as worker_module
    from shared.language_registry import default_language_registry

    class _FakeDb:
        async def update_language_images(self, *args, **kwargs) -> None:
            raise AssertionError("DB updates should not run when image sync is disabled")

        async def list_languages(self) -> list[dict[str, object]]:
            raise AssertionError("Language reload should not run when image sync is disabled")

    class _FakeImages:
        def get(self, image_ref: str):
            raise AssertionError("Docker image lookup should not run when image sync is disabled")

        def pull(self, image_ref: str):
            raise AssertionError("Docker pulls should not run when image sync is disabled")

    class _FakeDockerClient:
        images = _FakeImages()

    monkeypatch.setattr(worker_module.settings, "IMAGE_REGISTRY", "")
    monkeypatch.setattr(worker_module.settings, "IMAGE_NAMING", "path")
    monkeypatch.setattr(worker_module.settings, "IMAGE_TAG", "")
    monkeypatch.setattr(worker_module.settings, "IMAGE_PULL_POLICY", "missing")

    registry = default_language_registry()
    synced = await worker_module.sync_registry_images_from_settings(
        db=_FakeDb(),  # type: ignore[arg-type]
        docker_client=_FakeDockerClient(),  # type: ignore[arg-type]
        executor=None,  # type: ignore[arg-type]
        language_registry=registry,
    )

    assert synced == registry


@pytest.mark.asyncio
async def test_sync_registry_images_from_settings_pulls_and_updates_db(monkeypatch):
    """Configured image sync should pull canonical images and rewrite the DB rows."""
    from autojudge import worker as worker_module
    from shared.language_registry import default_language_registry

    source_language = default_language_registry()["python3"]
    updated_rows: list[dict[str, object]] = []
    update_calls: list[tuple[str, str, str]] = []

    class _FakeDb:
        async def update_language_images(
            self,
            language_id: str,
            *,
            compile_image: str,
            run_image: str,
        ) -> None:
            update_calls.append((language_id, compile_image, run_image))
            row = asdict(source_language)
            row["compile_image"] = compile_image
            row["run_image"] = run_image
            updated_rows[:] = [row]

        async def list_languages(self) -> list[dict[str, object]]:
            return list(updated_rows)

    ensured_images: list[str] = []

    async def _fake_ensure_image_available(
        docker_client,
        executor,
        image_ref: str,
    ) -> None:
        ensured_images.append(image_ref)

    import autojudge.image_sync as _image_sync_module

    monkeypatch.setattr(worker_module.settings, "IMAGE_REGISTRY", "ghcr.io/dclobato/noca")
    monkeypatch.setattr(worker_module.settings, "IMAGE_NAMING", "path")
    monkeypatch.setattr(worker_module.settings, "IMAGE_TAG", "v5.0.0")
    monkeypatch.setattr(worker_module.settings, "IMAGE_PULL_POLICY", "missing")
    monkeypatch.setattr(_image_sync_module, "_ensure_image_available", _fake_ensure_image_available)

    synced = await worker_module.sync_registry_images_from_settings(
        db=_FakeDb(),  # type: ignore[arg-type]
        docker_client=object(),  # type: ignore[arg-type]
        executor=None,  # type: ignore[arg-type]
        language_registry={"python3": source_language},
    )

    expected_compile = "ghcr.io/dclobato/noca/judge-python3:compile-v5.0.0"
    expected_run = "ghcr.io/dclobato/noca/judge-python3:run-v5.0.0"

    assert ensured_images == [expected_compile, expected_run]
    assert update_calls == [("python3", expected_compile, expected_run)]
    assert synced["python3"].compile_image == expected_compile
    assert synced["python3"].run_image == expected_run


@pytest.mark.asyncio
async def test_sync_registry_images_from_settings_supports_flat_naming(monkeypatch):
    """Configured image sync should support flat registry naming for Docker Hub."""
    from autojudge import worker as worker_module
    from shared.language_registry import default_language_registry

    source_language = default_language_registry()["python3"]
    ensured_images: list[str] = []
    updated_rows: list[dict[str, object]] = []
    update_calls: list[tuple[str, str, str]] = []

    class _FakeDb:
        async def update_language_images(
            self,
            language_id: str,
            *,
            compile_image: str,
            run_image: str,
        ) -> None:
            update_calls.append((language_id, compile_image, run_image))
            row = asdict(source_language)
            row["compile_image"] = compile_image
            row["run_image"] = run_image
            updated_rows[:] = [row]

        async def list_languages(self) -> list[dict[str, object]]:
            return list(updated_rows)

    async def _fake_ensure_image_available(
        docker_client,
        executor,
        image_ref: str,
    ) -> None:
        ensured_images.append(image_ref)

    import autojudge.image_sync as _image_sync_module

    monkeypatch.setattr(worker_module.settings, "IMAGE_REGISTRY", "docker.io/dclobato/noca")
    monkeypatch.setattr(worker_module.settings, "IMAGE_NAMING", "flat")
    monkeypatch.setattr(worker_module.settings, "IMAGE_TAG", "v6.2.2")
    monkeypatch.setattr(worker_module.settings, "IMAGE_PULL_POLICY", "missing")
    monkeypatch.setattr(_image_sync_module, "_ensure_image_available", _fake_ensure_image_available)

    synced = await worker_module.sync_registry_images_from_settings(
        db=_FakeDb(),  # type: ignore[arg-type]
        docker_client=object(),  # type: ignore[arg-type]
        executor=None,  # type: ignore[arg-type]
        language_registry={"python3": source_language},
    )

    expected_compile = "docker.io/dclobato/noca-judge-python3:compile-v6.2.2"
    expected_run = "docker.io/dclobato/noca-judge-python3:run-v6.2.2"

    assert ensured_images == [expected_compile, expected_run]
    assert update_calls == [("python3", expected_compile, expected_run)]
    assert synced["python3"].compile_image == expected_compile
    assert synced["python3"].run_image == expected_run


@pytest.mark.asyncio
async def test_worker_loop_publishes_last_job_after_dequeue(monkeypatch: pytest.MonkeyPatch) -> None:
    """publish_worker_last_job is called once when a job is successfully dequeued."""
    from autojudge import worker as worker_module

    shutdown_event = asyncio.Event()
    pause_flag = LivePauseFlag()
    fake_valkey = _FakeValkey()
    published_last_jobs: list[str] = []

    async def _fake_dequeue(_valkey: object) -> str:
        return "job-abc"

    async def _fake_dispatch(**_kwargs: object) -> None:
        shutdown_event.set()

    async def _fake_publish_last_job(client: object, *, worker_class: object, worker_id: str) -> None:
        published_last_jobs.append(worker_id)

    @asynccontextmanager
    async def _fake_open_db(_engine: object):
        yield object()

    monkeypatch.setattr(worker_module, "dequeue_job_id", _fake_dequeue)
    monkeypatch.setattr(worker_module, "_dispatch_job", _fake_dispatch)
    monkeypatch.setattr(worker_module, "open_db", _fake_open_db)
    monkeypatch.setattr(worker_module, "publish_worker_last_job", _fake_publish_last_job)

    await worker_module._worker_loop(
        slot=0,
        shutdown_event=shutdown_event,
        db_engine=object(),
        valkey=fake_valkey,
        pool_manager=object(),  # type: ignore[arg-type]
        language_registry={},
        docker_client=object(),  # type: ignore[arg-type]
        executor=object(),  # type: ignore[arg-type]
        wid="judge-1",
        pause_flag=pause_flag,
    )

    assert published_last_jobs == ["judge-1"]


@pytest.mark.asyncio
async def test_worker_loop_does_not_publish_last_job_on_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """publish_worker_last_job is NOT called when the queue is idle (job_id is None)."""
    from autojudge import worker as worker_module

    shutdown_event = asyncio.Event()
    pause_flag = LivePauseFlag()
    fake_valkey = _FakeValkey()
    published_last_jobs: list[str] = []
    call_count = 0

    async def _fake_dequeue(_valkey: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            shutdown_event.set()
        return None

    async def _fake_sleep(_delay: float) -> None:
        pass

    async def _fake_publish_last_job(client: object, *, worker_class: object, worker_id: str) -> None:
        published_last_jobs.append(worker_id)

    monkeypatch.setattr(worker_module, "dequeue_job_id", _fake_dequeue)
    monkeypatch.setattr(worker_module.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(worker_module, "publish_worker_last_job", _fake_publish_last_job)

    await worker_module._worker_loop(
        slot=0,
        shutdown_event=shutdown_event,
        db_engine=object(),
        valkey=fake_valkey,
        pool_manager=object(),  # type: ignore[arg-type]
        language_registry={},
        docker_client=object(),  # type: ignore[arg-type]
        executor=object(),  # type: ignore[arg-type]
        wid="judge-1",
        pause_flag=pause_flag,
    )

    assert published_last_jobs == []
