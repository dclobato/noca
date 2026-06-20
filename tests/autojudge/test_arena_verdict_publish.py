#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Both finalization exits of the Arena judge must publish an ArenaVerdictEvent."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import autojudge.arena_submission_job as job_module
from autojudge.arena_submission_job import process_arena_submission_job
from autojudge.types import ArenaQueuedTestCase, ProblemLimits, QueuedArenaSubmission, RepetitionCaseResult
from shared.enumerations import Verdict
from shared.language_registry import default_language_registry
from shared.queue_schema import ArenaVerdictEvent
from shared.services.valkey_service import ARENA_RESULTS_CHANNEL

_REGISTRY = default_language_registry()
_LANGUAGE_ID = next(iter(_REGISTRY))


def _submission() -> QueuedArenaSubmission:
    return QueuedArenaSubmission(
        judgment_id="judgment-1",
        submission_id="submission-1",
        user_id="user-1",
        problem_id="problem-1",
        problem_number=7,
        problem_title="Echo",
        language_id=_LANGUAGE_ID,
        source_code="x",
        limits=ProblemLimits(time_limit_ms=1000, memory_limit_kb=65536, pids_limit=16),
        test_cases=(ArenaQueuedTestCase(test_case_id="tc-1", ordinal=1, input_data=b"1\n", expected_output=b"1\n"),),
    )


def _published_events(valkey: AsyncMock) -> list[ArenaVerdictEvent]:
    events: list[ArenaVerdictEvent] = []
    for call in valkey.publish.await_args_list:
        channel, payload = call.args
        assert channel == ARENA_RESULTS_CHANNEL
        events.append(ArenaVerdictEvent.model_validate_json(payload))
    return events


@pytest.mark.asyncio
async def test_compile_error_exit_publishes_arena_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """The compile-error early return path publishes a CE ArenaVerdictEvent."""
    monkeypatch.setattr(
        job_module,
        "compile_submission",
        AsyncMock(return_value=SimpleNamespace(success=False, compile_log="boom", artifact_data=None)),
    )
    db = AsyncMock()
    valkey = AsyncMock()

    await process_arena_submission_job(
        submission=_submission(),
        db=db,
        valkey=valkey,
        pool_manager=AsyncMock(),
        language_registry=_REGISTRY,
        docker_client=SimpleNamespace(),
        executor=SimpleNamespace(),
        worker_id="worker-1",
    )

    db.set_arena_judgment_done.assert_awaited_once()
    events = _published_events(valkey)
    assert len(events) == 1
    assert events[0].verdict == Verdict.CE.value
    assert events[0].submission_id == "submission-1"
    assert events[0].judgment_id == "judgment-1"


@pytest.mark.asyncio
async def test_normal_verdict_exit_publishes_arena_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """The normal verdict path publishes the aggregated ArenaVerdictEvent."""
    monkeypatch.setattr(
        job_module,
        "compile_submission",
        AsyncMock(return_value=SimpleNamespace(success=True, compile_log="", artifact_data=b"artifact")),
    )
    monkeypatch.setattr(
        job_module,
        "_run_repeated_test_case",
        AsyncMock(
            return_value=RepetitionCaseResult(
                verdict=Verdict.AC,
                total_wall_time_ms=12,
                peak_memory_kb=2048,
                peak_output_bytes=2,
                peak_pids=1,
                exit_code=0,
                stdout_excerpt=b"1\n",
                stderr_excerpt=b"",
            )
        ),
    )
    db = AsyncMock()
    valkey = AsyncMock()
    pool_manager = AsyncMock()
    pool_manager.acquire = AsyncMock(return_value="container-1")
    pool_manager.release = AsyncMock()

    await process_arena_submission_job(
        submission=_submission(),
        db=db,
        valkey=valkey,
        pool_manager=pool_manager,
        language_registry=_REGISTRY,
        docker_client=SimpleNamespace(),
        executor=SimpleNamespace(),
        worker_id="worker-1",
    )

    db.set_arena_judgment_done.assert_awaited_once()
    events = _published_events(valkey)
    assert len(events) == 1
    assert events[0].verdict == Verdict.AC.value
    assert events[0].submission_id == "submission-1"
