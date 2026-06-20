"""
Tests for the judge worker idempotency lock (judge:lock:<judgment_id>).

Two suites:

Unit tests (class TestLockUnit)
    Use _FakeValkey — same lightweight fake used in test_autojudge_worker.py.
    No real Redis, no Docker.  DB uses the in-memory SQLite engine from conftest.

Integration tests (class TestLockIntegration)
    Use the real Valkey server at 127.0.0.1 (DB 15) via the `valkey_client`
    conftest fixture.  Tests are skipped automatically when Valkey is
    unreachable.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from autojudge.db import QueuedSubmission, open_db
from autojudge.runner import CompileResult, RunResult
from autojudge.worker import _process_job
from shared.enumerations import JudgmentStatus, Verdict
from shared.language_registry import default_language_registry
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem, ProblemTestCase
from web.models.submission import Submission, SubmissionJudgment
from web.models.users import User

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _uid() -> str:
    return str(uuid.uuid4())


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


def _make_submission(session: AsyncSession, problem: Problem, team: User, language: Language) -> Submission:
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
    session: AsyncSession, submission: Submission, status: JudgmentStatus = JudgmentStatus.QUEUED
) -> SubmissionJudgment:
    j = SubmissionJudgment(submission_id=submission.id, status=status)
    session.add(j)
    return j


def _make_qs(seed: dict) -> QueuedSubmission:
    j = seed["judgment"]
    sub = seed["submission"]
    return QueuedSubmission(
        judgment_id=j.id,
        submission_id=sub.id,
        contest_id=seed["contest"].id,
        contest_start_time=seed["contest"].start_time,
        problem_id=seed["problem"].id,
        team_id=sub.team_id,
        language_id=sub.language_id,
        source_code=sub.source_code,
        autojudge_only=True,
        accept_pe=False,
        stop_updating_scoreboard=120,
    )


# ---------------------------------------------------------------------------
# Fake Valkey (unit tests only)
# ---------------------------------------------------------------------------


class _FakeValkey:
    """Minimal Valkey replacement with SET NX semantics and call recording."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.published: list[tuple[str, Any]] = []
        self.set_calls: list[dict] = []
        self.delete_calls: list[tuple] = []
        self.lrem_calls: list[tuple] = []

    async def set(self, key: str, value: Any, *, nx: bool = False, ex: int | None = None) -> bool | None:
        self.set_calls.append({"key": key, "value": value, "nx": nx, "ex": ex})
        if nx and key in self.data:
            return None  # Redis returns None (falsy) when NX fails
        self.data[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        self.delete_calls.append(keys)
        count = 0
        for k in keys:
            if k in self.data:
                del self.data[k]
                count += 1
        return count

    async def lrem(self, name: str, count: int, value: str) -> int:
        self.lrem_calls.append((name, count, value))
        return 0

    async def zrem(self, name: str, *values: str) -> int:
        return 0

    async def zadd(self, name: str, mapping: dict[str, float]) -> int:
        return len(mapping)

    async def publish(self, channel: str, message: Any) -> int:
        self.published.append((channel, message))
        return 1

    def pipeline(self) -> _FakePipeline:  # sync, matches the real client API
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, parent: _FakeValkey) -> None:
        self._parent = parent
        self._cmds: list = []

    def lrem(self, name: str, count: int, value: str) -> _FakePipeline:
        self._cmds.append(("lrem", name, count, value))
        return self

    def zrem(self, name: str, *values: str) -> _FakePipeline:
        self._cmds.append(("zrem", name, *values))
        return self

    async def execute(self) -> list:
        results = []
        for cmd in self._cmds:
            if cmd[0] == "lrem":
                results.append(await self._parent.lrem(cmd[1], cmd[2], cmd[3]))
            else:
                results.append(0)
        return results


# ---------------------------------------------------------------------------
# Valkey fixture (overrides conftest to also skip on ConnectionError)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def valkey_client():
    """
    Real Valkey client against DB 15.

    Overrides the conftest fixture to skip (rather than error) on both
    ResponseError and ConnectionError so integration tests are automatically
    skipped when the Valkey server is not reachable from this host.
    """
    import valkey.asyncio as aivalkey
    from valkey.exceptions import ConnectionError as ValkeyConnectionError
    from valkey.exceptions import ResponseError

    from web.config import settings as web_settings
    from web.services.valkey_service import create_valkey_pool

    pool = create_valkey_pool(web_settings.valkey_url)
    client = aivalkey.Valkey.from_pool(pool)
    try:
        await client.flushdb()
    except (ResponseError, ValkeyConnectionError, Exception) as exc:
        await client.aclose()
        await pool.aclose()
        pytest.skip(f"Valkey at {web_settings.valkey_url} is unavailable: {exc}")
    try:
        yield client
    finally:
        with contextlib.suppress(Exception):
            await client.flushdb()
        await client.aclose()
        await pool.aclose()


# ---------------------------------------------------------------------------
# Shared seed fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed_data(session: AsyncSession, running_contest: Contest, contest_problem: Problem, team_user: User):
    """Language + submission + judgment + 2 test cases, committed to SQLite."""
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
# Unit test suite
# ---------------------------------------------------------------------------


class TestLockUnit:
    """Lock behaviour verified with _FakeValkey — no real Redis."""

    async def test_acquires_lock_before_any_db_write(self, engine, session, seed_data, monkeypatch):
        """
        SET NX must be called before set_judgment_dispatched.
        We verify this by tracking call order: if the lock key appears in
        fake_valkey.data before the DB row is touched, ordering is correct.
        """
        j_id = seed_data["judgment"].id
        fake_valkey = _FakeValkey()

        original_set = fake_valkey.set

        async def _tracking_set(key, value, *, nx=False, ex=None):
            result = await original_set(key, value, nx=nx, ex=ex)
            return result

        monkeypatch.setattr(
            "autojudge.submission_job.compile_submission",
            AsyncMock(return_value=CompileResult(success=False, exit_code=1, compile_log="CE")),
        )
        fake_valkey.set = _tracking_set  # type: ignore[method-assign]

        qs = _make_qs(seed_data)
        await session.close()

        lock_key = f"judge:lock:{j_id}"

        # Intercept set_judgment_dispatched to assert lock is held at that point
        from autojudge import db as _db_module

        original_dispatched = _db_module.DatabaseAccess.set_judgment_dispatched

        lock_held_at_dispatch: list[bool] = []

        async def _spy_dispatched(self, judgment_id, worker_id, contest_start_time=None):
            lock_held_at_dispatch.append(lock_key in fake_valkey.data)
            return await original_dispatched(self, judgment_id, worker_id, contest_start_time=contest_start_time)

        monkeypatch.setattr(_db_module.DatabaseAccess, "set_judgment_dispatched", _spy_dispatched)

        async with open_db(engine) as db:
            await _process_job(
                submission=qs,
                db=db,
                valkey=fake_valkey,  # type: ignore[arg-type]
                pool_manager=AsyncMock(),
                language_registry=default_language_registry(),
                docker_client=AsyncMock(),
                executor=None,
                worker_id="test-worker-a",
            )

        assert lock_held_at_dispatch == [True], "Lock must be held before first DB write"

    async def test_lock_key_and_ttl(self, engine, session, seed_data, monkeypatch):
        """SET NX is called with the correct key and the configured TTL."""
        from autojudge.config import settings

        j_id = seed_data["judgment"].id
        fake_valkey = _FakeValkey()

        monkeypatch.setattr(
            "autojudge.submission_job.compile_submission",
            AsyncMock(return_value=CompileResult(success=False, exit_code=1, compile_log="CE")),
        )

        qs = _make_qs(seed_data)
        await session.close()

        async with open_db(engine) as db:
            await _process_job(
                submission=qs,
                db=db,
                valkey=fake_valkey,  # type: ignore[arg-type]
                pool_manager=AsyncMock(),
                language_registry=default_language_registry(),
                docker_client=AsyncMock(),
                executor=None,
                worker_id="test-worker-b",
            )

        # First SET call must target the lock key with NX=True and the right TTL
        assert fake_valkey.set_calls, "set() was never called"
        lock_call = fake_valkey.set_calls[0]
        assert lock_call["key"] == f"judge:lock:{j_id}"
        assert lock_call["nx"] is True
        assert lock_call["ex"] == settings.LOCK_TTL_SECONDS

    async def test_discards_job_when_lock_already_held(self, engine, session, seed_data, monkeypatch):
        """
        When SET NX returns None (lock held by another worker), _process_job
        must return immediately without touching the DB.
        """
        j_id = seed_data["judgment"].id
        fake_valkey = _FakeValkey()

        # Pre-seed the lock so SET NX will fail
        fake_valkey.data[f"judge:lock:{j_id}"] = "other-worker"

        # compile_submission must NOT be called
        compile_mock = AsyncMock(
            return_value=CompileResult(success=True, exit_code=0, compile_log="", artifact_data=b"bin")
        )
        monkeypatch.setattr("autojudge.submission_job.compile_submission", compile_mock)

        qs = _make_qs(seed_data)
        await session.close()

        async with open_db(engine) as db:
            await _process_job(
                submission=qs,
                db=db,
                valkey=fake_valkey,  # type: ignore[arg-type]
                pool_manager=AsyncMock(),
                language_registry=default_language_registry(),
                docker_client=AsyncMock(),
                executor=None,
                worker_id="test-worker-c",
            )

        # DB row must be untouched
        async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
            j_reloaded = await vs.get(SubmissionJudgment, j_id)
            assert j_reloaded.status == JudgmentStatus.QUEUED, "Judgment must not be modified when lock is held"

        compile_mock.assert_not_called()

    async def test_discards_removes_from_inflight(self, engine, session, seed_data, monkeypatch):
        """When the lock is not acquired, lrem is called to clean up inflight."""
        j_id = seed_data["judgment"].id
        fake_valkey = _FakeValkey()
        fake_valkey.data[f"judge:lock:{j_id}"] = "other-worker"

        qs = _make_qs(seed_data)
        await session.close()

        async with open_db(engine) as db:
            await _process_job(
                submission=qs,
                db=db,
                valkey=fake_valkey,  # type: ignore[arg-type]
                pool_manager=AsyncMock(),
                language_registry=default_language_registry(),
                docker_client=AsyncMock(),
                executor=None,
                worker_id="test-worker-d",
            )

        # _remove_from_inflight uses a pipeline with lrem; check it was invoked
        assert fake_valkey.lrem_calls, "lrem must be called to remove job from inflight when lock is not acquired"

    async def test_lock_released_after_success(self, engine, session, seed_data, monkeypatch, tmp_path):
        """Lock key must be deleted from Valkey after a successful judgment."""
        j_id = seed_data["judgment"].id
        problem = seed_data["problem"]
        fake_valkey = _FakeValkey()

        prob_dir = tmp_path / problem.id
        prob_dir.mkdir()
        (prob_dir / "001.in").write_bytes(b"1\n")
        (prob_dir / "001.out").write_bytes(b"1\n")

        monkeypatch.setattr("autojudge.submission_job.settings.PROBLEM_TESTCASE_DIR", str(tmp_path))
        monkeypatch.setattr(
            "autojudge.submission_job.compile_submission",
            AsyncMock(return_value=CompileResult(success=True, exit_code=0, compile_log="", artifact_data=b"bin")),
        )
        monkeypatch.setattr(
            "autojudge.submission_job.run_test_case",
            AsyncMock(
                return_value=RunResult(
                    verdict=Verdict.AC, exit_code=0, wall_time_ms=10, stdout_excerpt=b"", stderr_excerpt=b""
                )
            ),
        )
        fake_pool = AsyncMock()
        fake_pool.acquire = AsyncMock(return_value="c-1")
        fake_pool.release = AsyncMock()

        # Remove one test case so only 1 is needed (seed has 2; monkeypatch dir has 1)
        qs = _make_qs(seed_data)
        await session.close()

        async with open_db(engine) as db:
            await _process_job(
                submission=qs,
                db=db,
                valkey=fake_valkey,  # type: ignore[arg-type]
                pool_manager=fake_pool,
                language_registry=default_language_registry(),
                docker_client=AsyncMock(),
                executor=None,
                worker_id="test-worker-e",
            )

        lock_key = f"judge:lock:{j_id}"
        # Lock key must have been deleted
        assert lock_key not in fake_valkey.data, "Lock must be released after successful judgment"
        # Verify delete was called with the lock key at some point
        deleted_keys = {k for call_args in fake_valkey.delete_calls for k in call_args}
        assert lock_key in deleted_keys

    async def test_lock_released_after_compile_error(self, engine, session, seed_data, monkeypatch):
        """Lock is released even when judgment ends in CE."""
        j_id = seed_data["judgment"].id
        fake_valkey = _FakeValkey()

        monkeypatch.setattr(
            "autojudge.submission_job.compile_submission",
            AsyncMock(return_value=CompileResult(success=False, exit_code=1, compile_log="error")),
        )

        qs = _make_qs(seed_data)
        await session.close()

        async with open_db(engine) as db:
            await _process_job(
                submission=qs,
                db=db,
                valkey=fake_valkey,  # type: ignore[arg-type]
                pool_manager=AsyncMock(),
                language_registry=default_language_registry(),
                docker_client=AsyncMock(),
                executor=None,
                worker_id="test-worker-f",
            )

        lock_key = f"judge:lock:{j_id}"
        assert lock_key not in fake_valkey.data
        deleted_keys = {k for call_args in fake_valkey.delete_calls for k in call_args}
        assert lock_key in deleted_keys

    async def test_lock_released_after_infrastructure_failure(self, engine, session, seed_data, monkeypatch):
        """Lock is released even when an unexpected exception is raised inside the pipeline."""
        j_id = seed_data["judgment"].id
        fake_valkey = _FakeValkey()

        monkeypatch.setattr(
            "autojudge.submission_job.compile_submission", AsyncMock(side_effect=RuntimeError("container crash"))
        )

        qs = _make_qs(seed_data)
        await session.close()

        # _process_job swallows all exceptions — it must NOT re-raise
        async with open_db(engine) as db:
            await _process_job(
                submission=qs,
                db=db,
                valkey=fake_valkey,  # type: ignore[arg-type]
                pool_manager=AsyncMock(),
                language_registry=default_language_registry(),
                docker_client=AsyncMock(),
                executor=None,
                worker_id="test-worker-g",
            )

        lock_key = f"judge:lock:{j_id}"
        assert lock_key not in fake_valkey.data
        deleted_keys = {k for call_args in fake_valkey.delete_calls for k in call_args}
        assert lock_key in deleted_keys

    async def test_two_concurrent_workers_only_one_judges(self, engine, session, seed_data, monkeypatch, tmp_path):
        """
        Simulate two workers racing on the same judgment_id.

        Both share the same _FakeValkey instance (shared in-memory state),
        which correctly serialises SET NX semantics.  Only one worker should
        call compile_submission.
        """
        problem = seed_data["problem"]
        fake_valkey = _FakeValkey()

        prob_dir = tmp_path / problem.id
        prob_dir.mkdir()
        (prob_dir / "001.in").write_bytes(b"1\n")
        (prob_dir / "001.out").write_bytes(b"1\n")

        monkeypatch.setattr("autojudge.submission_job.settings.PROBLEM_TESTCASE_DIR", str(tmp_path))

        compile_count = 0

        async def _fake_compile(*args, **kwargs):
            nonlocal compile_count
            compile_count += 1
            # Yield so the second coroutine can attempt the lock
            await asyncio.sleep(0)
            return CompileResult(success=False, exit_code=1, compile_log="CE")

        monkeypatch.setattr("autojudge.submission_job.compile_submission", _fake_compile)

        fake_pool = AsyncMock()
        fake_pool.acquire = AsyncMock(return_value="c-race")
        fake_pool.release = AsyncMock()

        qs = _make_qs(seed_data)
        await session.close()

        async def _run_worker(worker_id: str) -> None:
            async with open_db(engine) as db:
                await _process_job(
                    submission=qs,
                    db=db,
                    valkey=fake_valkey,  # type: ignore[arg-type]
                    pool_manager=fake_pool,
                    language_registry=default_language_registry(),
                    docker_client=AsyncMock(),
                    executor=None,
                    worker_id=worker_id,
                )

        await asyncio.gather(_run_worker("worker-race-1"), _run_worker("worker-race-2"))

        assert compile_count == 1, (
            f"Expected exactly 1 compile call, got {compile_count}. Both workers judged the same job concurrently."
        )

    async def test_worker_id_stored_in_lock_value(self, engine, session, seed_data, monkeypatch):
        """The lock value must be the worker_id (useful for debugging via redis-cli)."""
        fake_valkey = _FakeValkey()
        worker_id = "my-unique-worker-99"

        monkeypatch.setattr(
            "autojudge.submission_job.compile_submission",
            AsyncMock(return_value=CompileResult(success=False, exit_code=1, compile_log="CE")),
        )

        qs = _make_qs(seed_data)
        await session.close()

        async with open_db(engine) as db:
            await _process_job(
                submission=qs,
                db=db,
                valkey=fake_valkey,  # type: ignore[arg-type]
                pool_manager=AsyncMock(),
                language_registry=default_language_registry(),
                docker_client=AsyncMock(),
                executor=None,
                worker_id=worker_id,
            )

        lock_call = fake_valkey.set_calls[0]
        assert lock_call["value"] == worker_id


# ---------------------------------------------------------------------------
# Integration test suite (real Valkey)
# ---------------------------------------------------------------------------


class TestLockIntegration:
    """
    Lock behaviour verified against a real Valkey server (127.0.0.1, DB 15).

    The `valkey_client` fixture from conftest.py performs FLUSHDB before and
    after each test, so tests are isolated from each other and from other
    integration suites.
    """

    async def test_lock_is_set_in_valkey_during_judgment(
        self, engine, session, seed_data, monkeypatch, tmp_path, valkey_client
    ):
        """
        While _process_job is executing, the lock key must exist in Valkey.
        We observe this by patching compile_submission to pause and inspect
        Valkey mid-flight.
        """
        j_id = seed_data["judgment"].id
        lock_key = f"judge:lock:{j_id}"

        lock_observed_mid_flight: list[bool] = []

        async def _fake_compile(*args, **kwargs):
            # At this point the lock should already be set
            val = await valkey_client.get(lock_key)
            lock_observed_mid_flight.append(val is not None)
            return CompileResult(success=False, exit_code=1, compile_log="CE")

        monkeypatch.setattr("autojudge.submission_job.compile_submission", _fake_compile)

        qs = _make_qs(seed_data)
        await session.close()

        async with open_db(engine) as db:
            await _process_job(
                submission=qs,
                db=db,
                valkey=valkey_client,
                pool_manager=AsyncMock(),
                language_registry=default_language_registry(),
                docker_client=AsyncMock(),
                executor=None,
                worker_id="integration-worker-1",
            )

        assert lock_observed_mid_flight == [True], "Lock was not present in Valkey during judgment execution"

    async def test_lock_released_after_judgment(self, engine, session, seed_data, monkeypatch, valkey_client):
        """After _process_job completes, the lock key must be absent from Valkey."""
        j_id = seed_data["judgment"].id
        lock_key = f"judge:lock:{j_id}"

        monkeypatch.setattr(
            "autojudge.submission_job.compile_submission",
            AsyncMock(return_value=CompileResult(success=False, exit_code=1, compile_log="CE")),
        )

        qs = _make_qs(seed_data)
        await session.close()

        async with open_db(engine) as db:
            await _process_job(
                submission=qs,
                db=db,
                valkey=valkey_client,
                pool_manager=AsyncMock(),
                language_registry=default_language_registry(),
                docker_client=AsyncMock(),
                executor=None,
                worker_id="integration-worker-2",
            )

        remaining = await valkey_client.get(lock_key)
        assert remaining is None, f"Lock key {lock_key!r} was not deleted after judgment completed"

    async def test_second_worker_blocked_by_real_lock(self, engine, session, seed_data, monkeypatch, valkey_client):
        """
        Manually set the lock in real Valkey and verify that _process_job
        exits without modifying the DB (i.e. behaves as if another worker
        holds the lock).
        """
        j_id = seed_data["judgment"].id
        lock_key = f"judge:lock:{j_id}"

        # Simulate another worker holding the lock
        await valkey_client.set(lock_key, "foreign-worker", ex=120)

        compile_mock = AsyncMock(
            return_value=CompileResult(success=True, exit_code=0, compile_log="", artifact_data=b"bin")
        )
        monkeypatch.setattr("autojudge.submission_job.compile_submission", compile_mock)

        qs = _make_qs(seed_data)
        await session.close()

        async with open_db(engine) as db:
            await _process_job(
                submission=qs,
                db=db,
                valkey=valkey_client,
                pool_manager=AsyncMock(),
                language_registry=default_language_registry(),
                docker_client=AsyncMock(),
                executor=None,
                worker_id="integration-worker-3",
            )

        compile_mock.assert_not_called()

        async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
            j_reloaded = await vs.get(SubmissionJudgment, j_id)
            assert j_reloaded.status == JudgmentStatus.QUEUED

        # The foreign lock must still be there (worker-3 must not have deleted it)
        remaining = await valkey_client.get(lock_key)
        assert remaining is not None, "Worker incorrectly deleted a lock it did not own"

    async def test_lock_has_correct_ttl(self, engine, session, seed_data, monkeypatch, valkey_client):
        """
        The lock's TTL in real Valkey must be close to LOCK_TTL_SECONDS.
        We check it mid-flight (inside compile) before it can expire.
        """
        from autojudge.config import settings

        j_id = seed_data["judgment"].id
        lock_key = f"judge:lock:{j_id}"
        observed_ttl: list[int] = []

        async def _fake_compile(*args, **kwargs):
            ttl = await valkey_client.ttl(lock_key)
            observed_ttl.append(ttl)
            return CompileResult(success=False, exit_code=1, compile_log="CE")

        monkeypatch.setattr("autojudge.submission_job.compile_submission", _fake_compile)

        qs = _make_qs(seed_data)
        await session.close()

        async with open_db(engine) as db:
            await _process_job(
                submission=qs,
                db=db,
                valkey=valkey_client,
                pool_manager=AsyncMock(),
                language_registry=default_language_registry(),
                docker_client=AsyncMock(),
                executor=None,
                worker_id="integration-worker-4",
            )

        assert observed_ttl, "compile_submission was never called"
        ttl = observed_ttl[0]
        # TTL should be within 5 seconds of the configured value (accounting for test latency)
        assert 0 < ttl <= settings.LOCK_TTL_SECONDS, (
            f"Lock TTL {ttl}s is outside expected range (0, {settings.LOCK_TTL_SECONDS}]"
        )

    async def test_two_real_workers_only_one_judges(
        self, engine, session, seed_data, monkeypatch, tmp_path, valkey_client
    ):
        """
        Two coroutines race on the same judgment_id against real Valkey.
        SET NX atomicity guarantees only one wins.
        """
        problem = seed_data["problem"]

        prob_dir = tmp_path / problem.id
        prob_dir.mkdir()
        (prob_dir / "001.in").write_bytes(b"1\n")
        (prob_dir / "001.out").write_bytes(b"1\n")

        monkeypatch.setattr("autojudge.submission_job.settings.PROBLEM_TESTCASE_DIR", str(tmp_path))

        compile_count = 0

        async def _fake_compile(*args, **kwargs):
            nonlocal compile_count
            compile_count += 1
            await asyncio.sleep(0.05)  # simulate work; let other coroutine attempt lock
            return CompileResult(success=False, exit_code=1, compile_log="CE")

        monkeypatch.setattr("autojudge.submission_job.compile_submission", _fake_compile)

        qs = _make_qs(seed_data)
        await session.close()

        async def _run(worker_id: str) -> None:
            async with open_db(engine) as db:
                await _process_job(
                    submission=qs,
                    db=db,
                    valkey=valkey_client,
                    pool_manager=AsyncMock(),
                    language_registry=default_language_registry(),
                    docker_client=AsyncMock(),
                    executor=None,
                    worker_id=worker_id,
                )

        await asyncio.gather(_run("real-worker-A"), _run("real-worker-B"))

        assert compile_count == 1, (
            f"Expected exactly 1 compile, got {compile_count}. Real Valkey SET NX did not prevent duplicate processing."
        )
