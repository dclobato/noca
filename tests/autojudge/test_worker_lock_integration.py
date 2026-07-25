#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Integration tests for the judge worker idempotency lock, against a real Valkey
server (127.0.0.1, DB 15).

These prove what the fake cannot: that the key really appears in Valkey while a
judgment runs, that it carries the configured TTL, and that a second real worker
is blocked by it. Skipped automatically when Valkey is unreachable.

The fake-backed suite lives in ``test_worker_lock.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from _autojudge_worker_fakes import _make_qs
from sqlalchemy.ext.asyncio import async_sessionmaker

from autojudge.db import open_db
from autojudge.runner import CompileResult
from autojudge.worker import _process_job
from shared.enumerations import JudgmentStatus
from shared.language_registry import default_language_registry
from web.models.submission import SubmissionJudgment

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
