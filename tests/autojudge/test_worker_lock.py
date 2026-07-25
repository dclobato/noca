#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Unit tests for the judge worker idempotency lock (``judge:lock:<job_id>``).

The lock is what makes a requeue race harmless: whoever wins ``SET NX`` judges,
and everyone else discards the job untouched. These tests use ``_FakeValkey``
(no Valkey server, no Docker) with the in-memory SQLite engine from conftest.

The same behaviour against a real Valkey server lives in
``test_worker_lock_integration.py``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from _autojudge_worker_fakes import _FakeValkey, _make_qs
from sqlalchemy.ext.asyncio import async_sessionmaker

from autojudge.db import open_db
from autojudge.runner import CompileResult, RunResult
from autojudge.worker import _process_job
from shared.enumerations import JudgmentStatus, Verdict
from shared.language_registry import default_language_registry
from web.models.submission import SubmissionJudgment


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

        async def _spy_dispatched(self, judgment_id, worker_id, attempt_token, contest_start_time=None):
            lock_held_at_dispatch.append(lock_key in fake_valkey.data)
            return await original_dispatched(
                self, judgment_id, worker_id, attempt_token, contest_start_time=contest_start_time
            )

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
        """The lock token keeps the worker id while remaining unique per attempt."""
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
        assert lock_call["value"].startswith(f"{worker_id}:")
        assert len(lock_call["value"]) > len(worker_id) + 1
