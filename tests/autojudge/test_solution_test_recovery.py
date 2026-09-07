#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reaper → reconciliation regression test for the solution-test resurrection loop.

Deleting an exhausted job's hash would make the reconciler's ``not hash_data``
test true, so it would re-enqueue the still non-terminal run — forever. The reaper
therefore leaves a ``reaper_dropped=true`` tombstone and the reconciler
terminalizes the row before clearing it.

Uses real Valkey (``valkey_client`` fixture); skipped when Valkey is unavailable.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from autojudge.reaper import _reaper_cycle
from autojudge.reconcile import reconcile_queue_state
from autojudge.types import QueuedSolutionTestRun, RecoverableSolutionTestJob
from shared.enumerations import JudgmentStatus

INFLIGHT_KEY = "judge:queue:inflight"
INFLIGHT_TIMES_KEY = "judge:queue:inflight:times"
PENDING_KEY = "judge:queue:pending"
PRIORITY_KEY = "judge:queue:priority"
PROFILING_KEY = "judge:queue:profiling"
JOB_HASH_PREFIX = "judge:job"

RUN_ID = "solution-test-exhausted"
JOB_KEY = f"{JOB_HASH_PREFIX}:{RUN_ID}"
LOCK_KEY = f"judge:lock:{RUN_ID}"


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin queue keys and make every inflight entry immediately stale."""

    class _Settings:
        REAPER_INTERVAL_S = 0.2
        REAPER_STALE_THRESHOLD_MINUTES = 0.0
        PROFILING_REAPER_STALE_THRESHOLD_MINUTES = 0.0
        REAPER_MAX_REQUEUE_COUNT = 3
        queue_inflight_times_key = INFLIGHT_TIMES_KEY
        queue_inflight_key = INFLIGHT_KEY
        queue_pending_key = PENDING_KEY
        queue_priority_key = PRIORITY_KEY
        queue_profiling_key = PROFILING_KEY
        queue_job_hash_prefix = JOB_HASH_PREFIX

    from autojudge import reaper as reaper_module
    from autojudge import reconcile as reconcile_module

    monkeypatch.setattr(reaper_module, "settings", _Settings())
    monkeypatch.setattr(reconcile_module, "settings", _Settings())


class _FakeDb:
    """Reports the run as non-terminal until it is explicitly terminalized."""

    def __init__(self) -> None:
        self.status = JudgmentStatus.JUDGING
        self.terminalized: list[str] = []

    async def list_recoverable_submission_jobs(self) -> list[Any]:
        return []

    async def list_recoverable_profiling_jobs(self) -> list[Any]:
        return []

    async def list_recoverable_arena_submission_jobs(self) -> list[Any]:
        return []

    async def list_recoverable_custom_validator_jobs(self) -> list[Any]:
        return []

    async def list_recoverable_solution_test_jobs(self) -> list[RecoverableSolutionTestJob]:
        if self.status in {JudgmentStatus.DONE, JudgmentStatus.FAILED}:
            return []
        return [
            RecoverableSolutionTestJob(
                status=self.status,
                payload=QueuedSolutionTestRun(
                    solution_test_run_id=RUN_ID,
                    contest_id="contest-1",
                    problem_id="problem-1",
                    language_id="gcc-c17",
                    source_code="int main(){}",
                ),
            )
        ]

    async def fail_exhausted_solution_test_run(self, run_id: str) -> bool:
        self.terminalized.append(run_id)
        self.status = JudgmentStatus.FAILED
        return True


async def _seed_exhausted_job(valkey_client: Any) -> None:
    """Put the run in-flight with its retry budget already spent."""
    await valkey_client.zadd(INFLIGHT_TIMES_KEY, {RUN_ID: time.time() - 600})
    await valkey_client.rpush(INFLIGHT_KEY, RUN_ID)
    await valkey_client.set(LOCK_KEY, "dead-worker")
    await valkey_client.hset(
        JOB_KEY,
        mapping={
            "solution_test_run_id": RUN_ID,
            "contest_id": "contest-1",
            "problem_id": "problem-1",
            "language_id": "gcc-c17",
            "requeue_count": "3",
            "job_kind": "solution_test",
        },
    )


async def test_reaper_tombstones_instead_of_deleting_the_hash(valkey_client: Any) -> None:
    """An exhausted solution-test job keeps its hash, marked reaper_dropped."""
    await _seed_exhausted_job(valkey_client)

    requeued, dropped, already_done = await _reaper_cycle(valkey_client)

    assert (requeued, dropped, already_done) == (0, 1, 0)
    tombstone = await valkey_client.hgetall(JOB_KEY)
    assert tombstone, "deleting the hash would resurrect the run on every reconciliation"
    assert tombstone["reaper_dropped"] == "true"
    assert await valkey_client.lrange(INFLIGHT_KEY, 0, -1) == []
    assert await valkey_client.zscore(INFLIGHT_TIMES_KEY, RUN_ID) is None


async def test_reconciliation_terminalizes_the_tombstoned_run(valkey_client: Any) -> None:
    """The reconciler fails the run, clears its state, and never re-enqueues it."""
    await _seed_exhausted_job(valkey_client)
    await _reaper_cycle(valkey_client)

    db = _FakeDb()
    await reconcile_queue_state(db, valkey_client)  # type: ignore[arg-type]

    assert db.terminalized == [RUN_ID]
    assert db.status == JudgmentStatus.FAILED
    assert await valkey_client.hgetall(JOB_KEY) == {}
    assert await valkey_client.exists(LOCK_KEY) == 0
    assert await valkey_client.lrange(PENDING_KEY, 0, -1) == []
    assert await valkey_client.lrange(PRIORITY_KEY, 0, -1) == []

    # The second pass is the actual regression guard: with the hash gone and the
    # row terminal, nothing may be re-enqueued.
    await reconcile_queue_state(db, valkey_client)  # type: ignore[arg-type]

    assert db.terminalized == [RUN_ID]
    assert await valkey_client.lrange(PENDING_KEY, 0, -1) == []
    assert await valkey_client.lrange(PROFILING_KEY, 0, -1) == []


async def test_healthy_solution_test_run_is_re_enqueued(valkey_client: Any) -> None:
    """A non-terminal run missing from every queue is recovered onto pending."""
    db = _FakeDb()

    await reconcile_queue_state(db, valkey_client)  # type: ignore[arg-type]

    assert db.terminalized == []
    assert await valkey_client.lrange(PENDING_KEY, 0, -1) == [RUN_ID]
    job_hash = await valkey_client.hgetall(JOB_KEY)
    assert job_hash["job_kind"] == "solution_test"
    assert job_hash["contest_id"] == "contest-1"
