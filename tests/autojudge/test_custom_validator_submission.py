#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from unittest.mock import AsyncMock, Mock

import pytest

from autojudge import custom_validator_submission as service
from autojudge.custom_validator_submission import CustomValidatorUnavailableError, PreparedCustomValidator
from autojudge.interactive_runner import InteractiveAttemptResult
from autojudge.interactive_transcript import InteractiveTranscript
from autojudge.interactive_verdict import InteractiveVerdict
from autojudge.types import (
    ActiveCustomValidator,
    CompileResult,
    CustomValidatorDispatchState,
    ProblemLimits,
)
from shared.enumerations import CustomValidatorCrashReason, Verdict
from shared.language_registry import default_language_registry


def _result(
    verdict: Verdict | None,
    *,
    retryable: bool = False,
    crash: CustomValidatorCrashReason | None = None,
) -> InteractiveAttemptResult:
    return InteractiveAttemptResult(
        classification=InteractiveVerdict(verdict, retryable),
        contestant_exit_code=0,
        contestant_signal=None,
        validator_exit_code=0 if crash is None else None,
        validator_signal=None,
        transcript=InteractiveTranscript(lines=[], truncated=False),
        contestant_stderr_excerpt=b"",
        validator_stderr_excerpt=b"",
        contestant_output_bytes=0,
        crash_reason=crash,
    )


def _prepared() -> PreparedCustomValidator:
    language = default_language_registry()["python3"]
    return PreparedCustomValidator(
        language,
        CompileResult(success=True, exit_code=0, compile_log="", artifact_data=b"validator"),
    )


@pytest.mark.asyncio
async def test_prepare_rejects_configured_validator_without_active_revision() -> None:
    db = AsyncMock()
    db.get_custom_validator_dispatch_state.return_value = CustomValidatorDispatchState(True, None)
    with pytest.raises(CustomValidatorUnavailableError):
        await service.prepare_custom_validator(
            domain="contest",
            judgment_id="judgment",
            problem_id="problem",
            db=db,
            language_registry=default_language_registry(),
            docker_client=Mock(),
            executor=Mock(),
        )


@pytest.mark.asyncio
async def test_prepare_compiles_active_revision_for_each_submission(monkeypatch) -> None:
    db = AsyncMock()
    db.get_custom_validator_dispatch_state.return_value = CustomValidatorDispatchState(
        True,
        ActiveCustomValidator("python3", "print('validator')"),
    )
    compile_submission = AsyncMock(return_value=CompileResult(True, 0, "", b"artifact"))
    monkeypatch.setattr(service, "compile_submission", compile_submission)

    prepared = await service.prepare_custom_validator(
        domain="arena",
        judgment_id="judgment",
        problem_id="problem",
        db=db,
        language_registry=default_language_registry(),
        docker_client=Mock(),
        executor=Mock(),
    )

    assert prepared is not None
    assert prepared.compile_result.artifact_data == b"artifact"
    compile_submission.assert_awaited_once()


@pytest.mark.asyncio
async def test_retries_no_clean_exit_once_with_fresh_containers(monkeypatch) -> None:
    run = AsyncMock(
        side_effect=[
            _result(None, retryable=True, crash=CustomValidatorCrashReason.SIGNAL),
            _result(Verdict.AC),
        ]
    )
    monkeypatch.setattr(service, "run_docker_interaction", run)
    pool = AsyncMock()
    pool.acquire.side_effect = ["c1", "v1", "c2", "v2"]
    db = AsyncMock()

    result, _ = await service.run_custom_validator_submission(
        domain="contest",
        judgment_id="judgment",
        problem_id="problem",
        contestant_language=default_language_registry()["python3"],
        contestant_artifact=b"contestant",
        limits=ProblemLimits(1000, 65536, 16, 1024),
        db=db,
        pool_manager=pool,
        language_registry=default_language_registry(),
        docker_client=Mock(),
        executor=Mock(),
        prepared=_prepared(),
    )

    assert result is not None and result.classification.verdict == Verdict.AC
    assert run.await_count == 2
    assert db.insert_interactive_attempt.await_count == 2
    assert pool.release.await_count == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", [Verdict.RE, Verdict.MLE, Verdict.OLE])
async def test_clean_or_judge_limit_outcomes_are_not_retried(monkeypatch, verdict: Verdict) -> None:
    run = AsyncMock(return_value=_result(verdict))
    monkeypatch.setattr(service, "run_docker_interaction", run)
    pool = AsyncMock()
    pool.acquire.side_effect = ["c1", "v1"]

    result, _ = await service.run_custom_validator_submission(
        domain="arena",
        judgment_id="judgment",
        problem_id="problem",
        contestant_language=default_language_registry()["python3"],
        contestant_artifact=b"contestant",
        limits=ProblemLimits(1000, 65536, 16, 1024),
        db=AsyncMock(),
        pool_manager=pool,
        language_registry=default_language_registry(),
        docker_client=Mock(),
        executor=Mock(),
        prepared=_prepared(),
    )

    assert result is not None and result.classification.verdict == verdict
    assert run.await_count == 1


@pytest.mark.asyncio
async def test_startup_failure_is_persisted_and_retried_once(monkeypatch) -> None:
    monkeypatch.setattr(service, "run_docker_interaction", AsyncMock(side_effect=RuntimeError("start failed")))
    pool = AsyncMock()
    pool.acquire.side_effect = ["c1", "v1", "c2", "v2"]
    db = AsyncMock()

    result, _ = await service.run_custom_validator_submission(
        domain="arena",
        judgment_id="judgment",
        problem_id="problem",
        contestant_language=default_language_registry()["python3"],
        contestant_artifact=b"contestant",
        limits=ProblemLimits(1000, 65536, 16, 1024),
        db=db,
        pool_manager=pool,
        language_registry=default_language_registry(),
        docker_client=Mock(),
        executor=Mock(),
        prepared=_prepared(),
    )

    assert result is not None
    assert result.classification == InteractiveVerdict(None, True)
    assert result.crash_reason == CustomValidatorCrashReason.STARTUP
    # The bridge never ran, so there is no conversation to record.
    assert result.transcript is None
    assert db.insert_interactive_attempt.await_count == 2
