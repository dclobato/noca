#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Tests for autojudge.dispatch — routing one dequeued job to its pipeline.

Covers ``_dispatch_job`` job-kind routing, the legacy web→Arena fallback and
its terminal-state fence, the per-outcome ``jobs_completed_total`` metric
labels, and the Arena adapter's first-non-AC persistence rule.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from _autojudge_worker_fakes import (
    _FakeValkey,
    _jobs_completed_value,
    _uid,
)

from autojudge.runner import CompileResult
from autojudge.types import (
    ArenaQueuedTestCase,
    QueuedArenaSubmission,
    RepetitionCaseResult,
)
from shared.enumerations import Verdict
from shared.language_registry import default_language_registry


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
        limits=ProblemLimits(time_limit_ms=1000, memory_limit_kb=65536, pids_limit=16, output_limit_in_bytes=65536),
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
        attempt_token="worker-a:attempt",
    )

    fake_db.insert_arena_test_result.assert_awaited_once()
    kwargs = fake_db.insert_arena_test_result.await_args.kwargs
    assert kwargs["test_case_id"] == "case-2"
    assert kwargs["verdict"] == Verdict.WA
    fake_db.set_arena_judgment_done.assert_awaited_once()


async def test_dispatch_job_does_not_retry_a_fenced_web_judgment_against_arena(monkeypatch) -> None:
    """A terminal web judgment is not "missing" — it must not be retried as Arena.

    The fence raises a LookupError subclass, so the legacy Arena fallback has to
    tell the two cases apart or a fenced contest judgment would be judged again
    in the wrong domain.
    """
    from autojudge import dispatch as dispatch_module
    from autojudge import worker as worker_module
    from autojudge.types import JobNotDispatchable
    from shared.queue_schema import JobKind

    fake_valkey = _FakeValkey()
    job_id = _uid()
    fake_db = AsyncMock()
    fake_db.get_submission_for_judging = AsyncMock(return_value=AsyncMock())
    fake_db.get_arena_submission_for_judging = AsyncMock()
    monkeypatch.setattr(
        dispatch_module,
        "process_submission_job",
        AsyncMock(side_effect=JobNotDispatchable("judgment already terminal")),
    )
    arena_mock = AsyncMock()
    monkeypatch.setattr(dispatch_module, "process_arena_submission_job", arena_mock)

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

    assert _jobs_completed_value(JobKind.SUBMISSION, "lookup_error") - before == 1.0
    fake_db.get_arena_submission_for_judging.assert_not_awaited()
    arena_mock.assert_not_awaited()
    assert f"judge:lock:{job_id}" not in fake_valkey.data
    assert f"judge:job:{job_id}" not in fake_valkey.data


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


async def test_dispatch_job_abandons_job_when_ownership_is_lost(monkeypatch) -> None:
    """A lost claim must abort quietly, never persisting a terminal FAILED.

    Stamping FAILED here would bury the verdict the new owner is about to write,
    which is the corruption the claim exists to prevent.
    """
    from autojudge import dispatch as dispatch_module
    from autojudge import worker as worker_module
    from autojudge.types import JudgmentOwnershipLost
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
        AsyncMock(side_effect=JudgmentOwnershipLost("taken over")),
    )

    before_failed = _jobs_completed_value(JobKind.SUBMISSION, "failed")
    before_lost = _jobs_completed_value(JobKind.SUBMISSION, "ownership_lost")

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

    fake_db.set_judgment_failed.assert_not_awaited()
    assert _jobs_completed_value(JobKind.SUBMISSION, "failed") - before_failed == 0.0
    assert _jobs_completed_value(JobKind.SUBMISSION, "ownership_lost") - before_lost == 1.0
