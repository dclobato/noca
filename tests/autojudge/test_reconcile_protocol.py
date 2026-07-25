#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reconciliation protocol regression tests, parameterized over every job kind.

Reconciliation used to re-enqueue any job whose database row was DISPATCHED or
JUDGING, which handed live work to a second worker whose dispatch reset deletes
the first worker's partial results. These tests pin the replacement contract:
observed queue membership decides, and it is read atomically at decision time.

Uses real Valkey (``valkey_client`` fixture) because the decision now runs in a
Lua script; skipped when Valkey is unavailable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from autojudge.queue_ops import cleanup_claimed_job, dequeue_job_id
from autojudge.reconcile import reconcile_queue_state
from autojudge.types import (
    ArenaQueuedTestCase,
    ProblemLimits,
    QueuedArenaSubmission,
    QueuedProfilingRun,
    QueuedSolutionTestRun,
    QueuedSubmission,
    RecoverableArenaSubmissionJob,
    RecoverableCustomValidatorJob,
    RecoverableProfilingJob,
    RecoverableSolutionTestJob,
    RecoverableSubmissionJob,
)
from shared.enumerations import JudgmentStatus, ProfilingStatus
from shared.queue_schema import CustomValidatorValidationJob

INFLIGHT_KEY = "judge:queue:inflight"
INFLIGHT_TIMES_KEY = "judge:queue:inflight:times"
PENDING_KEY = "judge:queue:pending"
PRIORITY_KEY = "judge:queue:priority"
PROFILING_KEY = "judge:queue:profiling"
JOB_HASH_PREFIX = "judge:job"

ALL_QUEUE_KEYS = (PENDING_KEY, PRIORITY_KEY, PROFILING_KEY)


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the queue keys the reconciler reads."""

    class _Settings:
        queue_inflight_times_key = INFLIGHT_TIMES_KEY
        queue_inflight_key = INFLIGHT_KEY
        queue_pending_key = PENDING_KEY
        queue_priority_key = PRIORITY_KEY
        queue_profiling_key = PROFILING_KEY
        queue_job_hash_prefix = JOB_HASH_PREFIX

    from autojudge import reconcile as reconcile_module

    monkeypatch.setattr(reconcile_module, "settings", _Settings())


@dataclass(frozen=True)
class _Kind:
    """One reconcilable job kind, with the fake accessor that yields it."""

    name: str
    job_id: str
    accessor: str
    item: Any
    target_key: str


def _submission_kind() -> _Kind:
    return _Kind(
        name="submission",
        job_id="judgment-1",
        accessor="list_recoverable_submission_jobs",
        item=RecoverableSubmissionJob(
            status=JudgmentStatus.JUDGING,
            payload=QueuedSubmission(
                judgment_id="judgment-1",
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
        target_key=PENDING_KEY,
    )


def _profiling_kind() -> _Kind:
    return _Kind(
        name="profiling",
        job_id="profiling-1",
        accessor="list_recoverable_profiling_jobs",
        item=RecoverableProfilingJob(
            status=ProfilingStatus.RUNNING,
            payload=QueuedProfilingRun(
                profiling_run_id="profiling-1",
                contest_id="contest-1",
                problem_id="problem-1",
                language_id="gcc-c17",
                source_code="int main(){}",
                safety_factor=2.0,
            ),
        ),
        target_key=PROFILING_KEY,
    )


def _solution_test_kind() -> _Kind:
    return _Kind(
        name="solution_test",
        job_id="solution-test-1",
        accessor="list_recoverable_solution_test_jobs",
        item=RecoverableSolutionTestJob(
            status=JudgmentStatus.JUDGING,
            payload=QueuedSolutionTestRun(
                solution_test_run_id="solution-test-1",
                contest_id="contest-1",
                problem_id="problem-1",
                language_id="gcc-c17",
                source_code="int main(){}",
            ),
        ),
        target_key=PENDING_KEY,
    )


def _arena_submission_kind() -> _Kind:
    return _Kind(
        name="arena_submission",
        job_id="arena-judgment-1",
        accessor="list_recoverable_arena_submission_jobs",
        item=RecoverableArenaSubmissionJob(
            status=JudgmentStatus.JUDGING,
            payload=QueuedArenaSubmission(
                judgment_id="arena-judgment-1",
                submission_id="arena-submission-1",
                user_id="user-1",
                problem_id="arena-problem-1",
                problem_number=7,
                problem_title="Arena problem",
                language_id="gcc-c17",
                source_code="int main(){}",
                limits=ProblemLimits(
                    time_limit_ms=1000,
                    memory_limit_kb=262144,
                    pids_limit=16,
                    output_limit_in_bytes=1024,
                ),
                test_cases=(
                    ArenaQueuedTestCase(
                        test_case_id="arena-case-1",
                        ordinal=1,
                        input_data=b"1",
                        expected_output=b"1",
                    ),
                ),
            ),
        ),
        target_key=PENDING_KEY,
    )


def _custom_validator_kind() -> _Kind:
    return _Kind(
        name="custom_validator",
        job_id="validator-1",
        accessor="list_recoverable_custom_validator_jobs",
        item=RecoverableCustomValidatorJob(
            status="PENDING",
            payload=CustomValidatorValidationJob(
                validation_id="validator-1",
                domain="contest",
                problem_id="problem-1",
                candidate_token="token-1",
            ),
        ),
        target_key=PROFILING_KEY,
    )


ALL_KINDS = [
    _submission_kind(),
    _profiling_kind(),
    _solution_test_kind(),
    _arena_submission_kind(),
    _custom_validator_kind(),
]

_ACCESSORS = (
    "list_recoverable_submission_jobs",
    "list_recoverable_profiling_jobs",
    "list_recoverable_solution_test_jobs",
    "list_recoverable_arena_submission_jobs",
    "list_recoverable_custom_validator_jobs",
)


class _FakeDb:
    """Serves exactly one recoverable job, for one kind, and no others."""

    def __init__(self, kind: _Kind) -> None:
        self._kind = kind
        self.terminalized: list[str] = []

    def __getattr__(self, name: str) -> Any:
        if name not in _ACCESSORS:
            raise AttributeError(name)

        async def _list() -> list[Any]:
            return [self._kind.item] if name == self._kind.accessor else []

        return _list

    async def fail_exhausted_solution_test_run(self, run_id: str) -> bool:
        self.terminalized.append(run_id)
        return True

    async def reject_exhausted_custom_validator_validation(self, **kwargs: Any) -> bool:
        self.terminalized.append(str(kwargs.get("problem_id")))
        return True


async def _seed_hash(valkey_client: Any, kind: _Kind, **extra: str) -> None:
    """Write a plausible job hash for this kind."""
    await valkey_client.hset(
        f"{JOB_HASH_PREFIX}:{kind.job_id}",
        mapping={"requeue_count": "2", "job_kind": kind.name, **extra},
    )


async def _queue_members(valkey_client: Any) -> dict[str, list[str]]:
    """Return the contents of every queue list, for whole-state assertions."""
    return {key: await valkey_client.lrange(key, 0, -1) for key in (*ALL_QUEUE_KEYS, INFLIGHT_KEY)}


@pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda k: k.name)
async def test_inflight_job_is_never_reclaimed(kind: _Kind, valkey_client: Any) -> None:
    """A job a worker is executing keeps its lock and stays out of the queues."""
    lock_key = f"judge:lock:{kind.job_id}"
    await valkey_client.rpush(INFLIGHT_KEY, kind.job_id)
    await valkey_client.zadd(INFLIGHT_TIMES_KEY, {kind.job_id: time.time()})
    await valkey_client.set(lock_key, "live-worker")
    await _seed_hash(valkey_client, kind)

    await reconcile_queue_state(_FakeDb(kind), valkey_client, phase="Periodic")  # type: ignore[arg-type]

    members = await _queue_members(valkey_client)
    assert members[INFLIGHT_KEY] == [kind.job_id]
    for key in ALL_QUEUE_KEYS:
        assert members[key] == []
    assert await valkey_client.get(lock_key) == "live-worker"
    assert await valkey_client.zscore(INFLIGHT_TIMES_KEY, kind.job_id) is not None


@pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda k: k.name)
async def test_inflight_without_a_lock_is_still_left_to_the_reaper(kind: _Kind, valkey_client: Any) -> None:
    """The gap between dequeue and lock acquisition must not look like an orphan."""
    await valkey_client.rpush(INFLIGHT_KEY, kind.job_id)
    await valkey_client.zadd(INFLIGHT_TIMES_KEY, {kind.job_id: time.time()})
    await _seed_hash(valkey_client, kind)

    await reconcile_queue_state(_FakeDb(kind), valkey_client, phase="Periodic")  # type: ignore[arg-type]

    members = await _queue_members(valkey_client)
    assert members[INFLIGHT_KEY] == [kind.job_id]
    assert all(members[key] == [] for key in ALL_QUEUE_KEYS)


@pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda k: k.name)
async def test_inflight_without_deadline_gets_one(kind: _Kind, valkey_client: Any) -> None:
    """A missing reaper deadline is healed so stale work stays reclaimable."""
    await valkey_client.rpush(INFLIGHT_KEY, kind.job_id)
    await valkey_client.set(f"judge:lock:{kind.job_id}", "live-worker")
    await _seed_hash(valkey_client, kind)
    before = time.time()

    await reconcile_queue_state(_FakeDb(kind), valkey_client, phase="Periodic")  # type: ignore[arg-type]

    score = await valkey_client.zscore(INFLIGHT_TIMES_KEY, kind.job_id)
    assert score is not None and score >= before
    assert await valkey_client.lrange(INFLIGHT_KEY, 0, -1) == [kind.job_id]


@pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda k: k.name)
async def test_existing_deadline_is_not_moved(kind: _Kind, valkey_client: Any) -> None:
    """Healing must never push the reaper's deadline forward on every pass."""
    original = time.time() - 600
    await valkey_client.rpush(INFLIGHT_KEY, kind.job_id)
    await valkey_client.zadd(INFLIGHT_TIMES_KEY, {kind.job_id: original})
    await _seed_hash(valkey_client, kind)

    await reconcile_queue_state(_FakeDb(kind), valkey_client, phase="Periodic")  # type: ignore[arg-type]

    assert await valkey_client.zscore(INFLIGHT_TIMES_KEY, kind.job_id) == pytest.approx(original)


@pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda k: k.name)
async def test_locked_job_not_in_any_queue_is_left_alone(kind: _Kind, valkey_client: Any) -> None:
    """The cleanup tail — out of inflight, lock still held — is not an orphan."""
    lock_key = f"judge:lock:{kind.job_id}"
    await valkey_client.set(lock_key, "live-worker")
    await _seed_hash(valkey_client, kind)

    await reconcile_queue_state(_FakeDb(kind), valkey_client, phase="Periodic")  # type: ignore[arg-type]

    members = await _queue_members(valkey_client)
    assert all(members[key] == [] for key in (*ALL_QUEUE_KEYS, INFLIGHT_KEY))
    assert await valkey_client.get(lock_key) == "live-worker"


@pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda k: k.name)
async def test_queued_job_is_not_duplicated(
    kind: _Kind,
    valkey_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A job already waiting in its queue is never pushed a second time."""
    from autojudge import reconcile as reconcile_module

    await valkey_client.rpush(kind.target_key, kind.job_id)
    await _seed_hash(valkey_client, kind)
    atomic_decision = pytest.fail

    async def _unexpected_atomic_decision(*args: Any, **kwargs: Any) -> str:
        atomic_decision("Steady-state queued jobs must use the O(1) snapshot fast path")

    monkeypatch.setattr(reconcile_module, "reconcile_job_state", _unexpected_atomic_decision)

    await reconcile_queue_state(_FakeDb(kind), valkey_client, phase="Periodic")  # type: ignore[arg-type]

    assert await valkey_client.lrange(kind.target_key, 0, -1) == [kind.job_id]


@pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda k: k.name)
async def test_queued_job_with_missing_hash_is_repaired_in_place(kind: _Kind, valkey_client: Any) -> None:
    """A queued job whose hash vanished gets it back without a second copy."""
    await valkey_client.rpush(kind.target_key, kind.job_id)

    await reconcile_queue_state(_FakeDb(kind), valkey_client, phase="Periodic")  # type: ignore[arg-type]

    assert await valkey_client.lrange(kind.target_key, 0, -1) == [kind.job_id]
    assert await valkey_client.hgetall(f"{JOB_HASH_PREFIX}:{kind.job_id}") != {}


@pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda k: k.name)
async def test_priority_queued_submission_is_not_duplicated(kind: _Kind, valkey_client: Any) -> None:
    """Membership of the priority queue counts as queued for every kind."""
    await valkey_client.rpush(PRIORITY_KEY, kind.job_id)
    await _seed_hash(valkey_client, kind)

    await reconcile_queue_state(_FakeDb(kind), valkey_client, phase="Periodic")  # type: ignore[arg-type]

    members = await _queue_members(valkey_client)
    assert members[PRIORITY_KEY] == [kind.job_id]
    assert members[kind.target_key] == []


@pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda k: k.name)
async def test_orphan_absent_everywhere_is_recovered(kind: _Kind, valkey_client: Any) -> None:
    """The job reconciliation exists for: committed, non-terminal, not queued."""
    await reconcile_queue_state(_FakeDb(kind), valkey_client, phase="Startup")  # type: ignore[arg-type]

    assert await valkey_client.lrange(kind.target_key, 0, -1) == [kind.job_id]
    assert await valkey_client.hgetall(f"{JOB_HASH_PREFIX}:{kind.job_id}") != {}


@pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda k: k.name)
async def test_orphan_recovery_preserves_the_requeue_count(kind: _Kind, valkey_client: Any) -> None:
    """A recovered job keeps its retry budget so the reaper can still exhaust it."""
    await _seed_hash(valkey_client, kind)

    await reconcile_queue_state(_FakeDb(kind), valkey_client, phase="Startup")  # type: ignore[arg-type]

    job_hash = await valkey_client.hgetall(f"{JOB_HASH_PREFIX}:{kind.job_id}")
    assert job_hash["requeue_count"] == "2"


@pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda k: k.name)
async def test_repeated_passes_over_a_live_job_recover_nothing(kind: _Kind, valkey_client: Any) -> None:
    """The periodic loop must not accumulate copies of work already running."""
    await valkey_client.rpush(INFLIGHT_KEY, kind.job_id)
    await valkey_client.zadd(INFLIGHT_TIMES_KEY, {kind.job_id: time.time()})
    await valkey_client.set(f"judge:lock:{kind.job_id}", "live-worker")
    await _seed_hash(valkey_client, kind)

    db = _FakeDb(kind)
    for _ in range(3):
        await reconcile_queue_state(db, valkey_client, phase="Periodic")  # type: ignore[arg-type]

    members = await _queue_members(valkey_client)
    assert members[INFLIGHT_KEY] == [kind.job_id]
    assert all(members[key] == [] for key in ALL_QUEUE_KEYS)


@pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda k: k.name)
async def test_job_dequeued_after_the_pass_begins_is_not_reclaimed(kind: _Kind, valkey_client: Any) -> None:
    """Queue state is read after the DB status, so a mid-pass dequeue is seen.

    Regression test for the snapshot-staleness race: the job is pending when the
    pass starts and a worker wins it before the reconciler reaches its decision.
    """
    await valkey_client.rpush(kind.target_key, kind.job_id)
    await _seed_hash(valkey_client, kind)

    class _RacingDb(_FakeDb):
        """Lets a worker dequeue and lock the job while the DB row is read."""

        def __getattr__(self, name: str) -> Any:
            lister = super().__getattr__(name)

            async def _list() -> list[Any]:
                items = await lister()
                if items:
                    assert await dequeue_job_id(valkey_client) == kind.job_id
                    await valkey_client.set(f"judge:lock:{kind.job_id}", "racing-worker")
                return items

            return _list

    await reconcile_queue_state(_RacingDb(kind), valkey_client, phase="Periodic")  # type: ignore[arg-type]

    members = await _queue_members(valkey_client)
    assert members[INFLIGHT_KEY] == [kind.job_id]
    assert all(members[key] == [] for key in ALL_QUEUE_KEYS)
    assert await valkey_client.get(f"judge:lock:{kind.job_id}") == "racing-worker"


async def test_mixed_pass_recovers_only_the_orphans(valkey_client: Any) -> None:
    """One pass over several kinds touches the orphans and nothing else."""
    orphan = _submission_kind()
    healthy = _profiling_kind()
    live = _solution_test_kind()

    await valkey_client.rpush(PROFILING_KEY, healthy.job_id)
    await _seed_hash(valkey_client, healthy)
    await valkey_client.rpush(INFLIGHT_KEY, live.job_id)
    await valkey_client.zadd(INFLIGHT_TIMES_KEY, {live.job_id: time.time()})
    await valkey_client.set(f"judge:lock:{live.job_id}", "live-worker")
    await _seed_hash(valkey_client, live)

    class _MultiDb(_FakeDb):
        def __getattr__(self, name: str) -> Any:
            async def _list() -> list[Any]:
                for kind in (orphan, healthy, live):
                    if name == kind.accessor:
                        return [kind.item]
                return []

            if name not in _ACCESSORS:
                raise AttributeError(name)
            return _list

    await reconcile_queue_state(_MultiDb(orphan), valkey_client, phase="Startup")  # type: ignore[arg-type]

    members = await _queue_members(valkey_client)
    assert members[PENDING_KEY] == [orphan.job_id]
    assert members[PROFILING_KEY] == [healthy.job_id]
    assert members[INFLIGHT_KEY] == [live.job_id]
    assert await valkey_client.get(f"judge:lock:{live.job_id}") == "live-worker"


async def test_tombstoned_validator_candidate_is_rejected_with_its_token(valkey_client: Any) -> None:
    """A reaper-dropped validation job becomes INVALID instead of requeued forever."""
    kind = _custom_validator_kind()
    rejected: list[dict[str, str]] = []

    class _ValidatorDb(_FakeDb):
        async def reject_exhausted_custom_validator_validation(
            self,
            *,
            domain: str,
            problem_id: str,
            candidate_token: str,
        ) -> bool:
            rejected.append({"domain": domain, "problem_id": problem_id, "candidate_token": candidate_token})
            return True

    await _seed_hash(valkey_client, kind, reaper_dropped="true")

    await reconcile_queue_state(_ValidatorDb(kind), valkey_client, phase="Startup")  # type: ignore[arg-type]

    assert rejected == [{"domain": "contest", "problem_id": "problem-1", "candidate_token": "token-1"}]
    assert await valkey_client.hgetall(f"{JOB_HASH_PREFIX}:{kind.job_id}") == {}
    assert await valkey_client.lrange(PROFILING_KEY, 0, -1) == []


async def test_tombstoned_run_terminalizes_even_with_a_stale_lock(valkey_client: Any) -> None:
    """A reaper-dropped job reaches a terminal state before any membership check."""
    kind = _solution_test_kind()
    await valkey_client.rpush(INFLIGHT_KEY, kind.job_id)
    await valkey_client.zadd(INFLIGHT_TIMES_KEY, {kind.job_id: time.time()})
    await valkey_client.set(f"judge:lock:{kind.job_id}", "dead-worker")
    await _seed_hash(valkey_client, kind, reaper_dropped="true")

    db = _FakeDb(kind)
    await reconcile_queue_state(db, valkey_client, phase="Periodic")  # type: ignore[arg-type]

    assert db.terminalized == [kind.job_id]
    members = await _queue_members(valkey_client)
    assert all(members[key] == [] for key in (*ALL_QUEUE_KEYS, INFLIGHT_KEY))
    assert await valkey_client.exists(f"judge:lock:{kind.job_id}") == 0
    assert await valkey_client.hgetall(f"{JOB_HASH_PREFIX}:{kind.job_id}") == {}


async def test_tombstone_created_during_reconciliation_is_not_resurrected(
    valkey_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The atomic decision sees a tombstone written after the preliminary hash read."""
    from autojudge import queue_ops as queue_ops_module
    from autojudge import reconcile as reconcile_module

    kind = _solution_test_kind()
    original = queue_ops_module.reconcile_job_state

    async def _tombstone_then_decide(*args: Any, **kwargs: Any) -> str:
        await valkey_client.hset(
            f"{JOB_HASH_PREFIX}:{kind.job_id}",
            mapping={
                "job_kind": "solution_test",
                "requeue_count": "3",
                "reaper_dropped": "true",
            },
        )
        return await original(*args, **kwargs)

    monkeypatch.setattr(reconcile_module, "reconcile_job_state", _tombstone_then_decide)
    db = _FakeDb(kind)

    await reconcile_queue_state(db, valkey_client, phase="Periodic")  # type: ignore[arg-type]

    assert db.terminalized == [kind.job_id]
    assert await valkey_client.lrange(PENDING_KEY, 0, -1) == []
    assert await valkey_client.hgetall(f"{JOB_HASH_PREFIX}:{kind.job_id}") == {}


async def test_reconciliation_clears_a_stale_deadline_from_queued_work(valkey_client: Any) -> None:
    """A stale score cannot make the reaper reclaim a healthy queued job."""
    from autojudge.reaper import _reaper_cycle

    kind = _submission_kind()
    await valkey_client.rpush(PENDING_KEY, kind.job_id)
    await valkey_client.zadd(INFLIGHT_TIMES_KEY, {kind.job_id: time.time() - 3600})
    await _seed_hash(valkey_client, kind)

    await reconcile_queue_state(_FakeDb(kind), valkey_client, phase="Periodic")  # type: ignore[arg-type]
    requeued, dropped, already_done = await _reaper_cycle(valkey_client)

    assert (requeued, dropped, already_done) == (0, 0, 0)
    assert await valkey_client.zscore(INFLIGHT_TIMES_KEY, kind.job_id) is None
    assert await valkey_client.lrange(PENDING_KEY, 0, -1) == [kind.job_id]


async def test_claimed_cleanup_never_deletes_a_new_attempt(valkey_client: Any) -> None:
    """An expired attempt cannot remove state protected by a replacement token."""
    kind = _submission_kind()
    lock_key = f"judge:lock:{kind.job_id}"
    job_key = f"{JOB_HASH_PREFIX}:{kind.job_id}"
    await valkey_client.rpush(INFLIGHT_KEY, kind.job_id)
    await valkey_client.zadd(INFLIGHT_TIMES_KEY, {kind.job_id: time.time()})
    await valkey_client.set(lock_key, "new-attempt")
    await _seed_hash(valkey_client, kind)

    assert (
        await cleanup_claimed_job(
            valkey_client,
            job_id=kind.job_id,
            lock_token="expired-attempt",
        )
        == "not_owner"
    )
    assert await valkey_client.get(lock_key) == "new-attempt"
    assert await valkey_client.hgetall(job_key) != {}
    assert await valkey_client.lrange(INFLIGHT_KEY, 0, -1) == [kind.job_id]

    assert (
        await cleanup_claimed_job(
            valkey_client,
            job_id=kind.job_id,
            lock_token="new-attempt",
        )
        == "cleaned"
    )
    assert await valkey_client.exists(lock_key) == 0
    assert await valkey_client.hgetall(job_key) == {}
    assert await valkey_client.lrange(INFLIGHT_KEY, 0, -1) == []
