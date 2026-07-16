#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import json
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from autojudge import custom_validator_submission as service
from autojudge.box_registry import registry as box_registry
from autojudge.custom_validator_submission import CustomValidatorUnavailableError, PreparedCustomValidator
from autojudge.interactive_runner import InteractiveAttemptResult
from autojudge.interactive_transcript import InteractiveTranscript
from autojudge.interactive_verdict import InteractiveVerdict
from autojudge.sandbox import build_validator_isolate_command
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


async def _run(
    *,
    pool: AsyncMock,
    db: AsyncMock,
    test_cases: list[tuple[int, bytes]] | None = None,
    domain: str = "contest",
    user_language_id: str = "python3",
    per_language_limits: dict[str, ProblemLimits] | None = None,
) -> tuple[InteractiveAttemptResult | None, CompileResult | None]:
    """Run the interactive judgment over ``test_cases`` (one case by default)."""
    return await service.run_custom_validator_submission(
        domain=domain,
        judgment_id="judgment",
        problem_id="problem",
        contestant_language=default_language_registry()["python3"],
        contestant_artifact=b"contestant",
        limits=ProblemLimits(1000, 65536, 16, 1024),
        test_cases=[(1, b"case-1\n")] if test_cases is None else test_cases,
        db=db,
        pool_manager=pool,
        language_registry=default_language_registry(),
        docker_client=Mock(),
        executor=Mock(),
        prepared=_prepared(),
        user_language_id=user_language_id,
        per_language_limits=per_language_limits,
    )


def test_build_validator_environment_exposes_effective_limits() -> None:
    environment = service.build_validator_environment(
        limits=ProblemLimits(1000, 65536, 16, None),
        user_language_id="python3",
    )

    assert environment == {
        "PROBLEM_TIME_LIMIT": "1000",
        "PROBLEM_OUTPUT_LIMIT": str(service.settings.OUTPUT_LIMIT_BYTES),
        "PROBLEM_MEMORY_LIMIT": "65536",
        "PROBLEM_PID_LIMIT": "16",
        "USER_LANGUAGE": "python3",
    }


def test_validator_environment_is_forwarded_through_isolate() -> None:
    # isolate starts the sandboxed process with an empty environment, so variables set
    # on the enclosing Docker exec never reach the validator: they must be on its command.
    container = Mock(id="validator-container")
    box_registry.allocate(container.id)

    command = build_validator_isolate_command(
        container,
        default_language_registry()["python3"],
        {"USER_LANGUAGE": "cpp", "PER_LANGUAGE_LIMITS": '{"cpp": {"time_limit_ms": 2000}}'},
    )

    assert "--env=USER_LANGUAGE=cpp" in command
    assert '--env=PER_LANGUAGE_LIMITS={"cpp": {"time_limit_ms": 2000}}' in command
    # Every variable must precede the "--" that ends isolate's own options.
    assert all(command.index(option) < command.index("--") for option in command if option.startswith("--env="))


def test_build_validator_environment_includes_web_per_language_limits() -> None:
    environment = service.build_validator_environment(
        limits=ProblemLimits(1000, 65536, 16, 1024),
        user_language_id="cpp",
        per_language_limits={
            "cpp": ProblemLimits(2000, 131072, 32, 4096, repetitions=3),
            "python3": ProblemLimits(3000, 262144, 64, None),
        },
    )

    assert environment["USER_LANGUAGE"] == "cpp"
    assert json.loads(environment["PER_LANGUAGE_LIMITS"]) == {
        "cpp": {
            "time_limit_ms": 2000,
            "memory_limit_kb": 131072,
            "pids_limit": 32,
            "output_limit_in_bytes": 4096,
            "repetitions": 3,
        },
        "python3": {
            "time_limit_ms": 3000,
            "memory_limit_kb": 262144,
            "pids_limit": 64,
            "output_limit_in_bytes": service.settings.OUTPUT_LIMIT_BYTES,
            "repetitions": 1,
        },
    }


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
    monkeypatch.setattr(service, "prepare_interactive_containers", AsyncMock())
    pool = AsyncMock()
    pool.acquire.side_effect = ["c1", "v1", "c2", "v2"]
    db = AsyncMock()

    result, _ = await _run(pool=pool, db=db)

    assert result is not None and result.classification.verdict == Verdict.AC
    assert run.await_count == 2
    assert db.insert_interactive_attempt.await_count == 2
    # The failed attempt's containers are killed, so the retry takes a fresh pair.
    assert pool.release.await_count == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", [Verdict.RE, Verdict.MLE, Verdict.OLE])
async def test_clean_or_judge_limit_outcomes_are_not_retried(monkeypatch, verdict: Verdict) -> None:
    run = AsyncMock(return_value=_result(verdict))
    monkeypatch.setattr(service, "run_docker_interaction", run)
    monkeypatch.setattr(service, "prepare_interactive_containers", AsyncMock())
    pool = AsyncMock()
    pool.acquire.side_effect = ["c1", "v1"]

    result, _ = await _run(pool=pool, db=AsyncMock(), domain="arena")

    assert result is not None and result.classification.verdict == verdict
    assert run.await_count == 1


@pytest.mark.asyncio
async def test_startup_failure_is_persisted_and_retried_once(monkeypatch) -> None:
    monkeypatch.setattr(service, "run_docker_interaction", AsyncMock(side_effect=RuntimeError("start failed")))
    monkeypatch.setattr(service, "prepare_interactive_containers", AsyncMock())
    pool = AsyncMock()
    pool.acquire.side_effect = ["c1", "v1", "c2", "v2"]
    db = AsyncMock()

    result, _ = await _run(pool=pool, db=db, domain="arena")

    assert result is not None
    assert result.classification == InteractiveVerdict(None, True)
    assert result.crash_reason == CustomValidatorCrashReason.STARTUP
    # The bridge never ran, so there is no conversation to record.
    assert result.transcript is None
    assert db.insert_interactive_attempt.await_count == 2


@pytest.mark.asyncio
async def test_each_test_case_parametrizes_the_validator_until_all_pass(monkeypatch) -> None:
    run = AsyncMock(return_value=_result(Verdict.AC))
    monkeypatch.setattr(service, "run_docker_interaction", run)
    prepare = AsyncMock()
    monkeypatch.setattr(service, "prepare_interactive_containers", prepare)
    pool = AsyncMock()
    pool.acquire.side_effect = ["c1", "v1"]
    db = AsyncMock()

    result, _ = await _run(
        pool=pool,
        db=db,
        test_cases=[(1, b"first\n"), (2, b"second\n"), (3, b"third\n")],
    )

    assert result is not None and result.classification.verdict == Verdict.AC
    assert run.await_count == 3
    assert [call.kwargs["testcase_input"] for call in run.await_args_list] == [b"first\n", b"second\n", b"third\n"]
    # One container pair judges the whole submission; artifacts are copied in once.
    assert pool.acquire.await_count == 2
    assert prepare.await_count == 1
    assert [call.kwargs["test_case_ordinal"] for call in db.insert_interactive_attempt.await_args_list] == [1, 2, 3]


@pytest.mark.asyncio
async def test_validator_environment_is_passed_to_each_interactive_attempt(monkeypatch) -> None:
    run = AsyncMock(return_value=_result(Verdict.AC))
    monkeypatch.setattr(service, "run_docker_interaction", run)
    monkeypatch.setattr(service, "prepare_interactive_containers", AsyncMock())
    pool = AsyncMock()
    pool.acquire.side_effect = ["c1", "v1"]

    result, _ = await _run(
        pool=pool,
        db=AsyncMock(),
        user_language_id="cpp",
        per_language_limits={"cpp": ProblemLimits(2500, 131072, 32, 2048)},
    )

    assert result is not None and result.classification.verdict == Verdict.AC
    environment = run.await_args.kwargs["validator_environment"]
    assert environment["USER_LANGUAGE"] == "cpp"
    assert environment["PROBLEM_TIME_LIMIT"] == "1000"
    assert json.loads(environment["PER_LANGUAGE_LIMITS"])["cpp"] == {
        "time_limit_ms": 2500,
        "memory_limit_kb": 131072,
        "pids_limit": 32,
        "output_limit_in_bytes": 2048,
        "repetitions": 1,
    }


@pytest.mark.asyncio
async def test_iteration_stops_at_the_first_case_that_does_not_pass(monkeypatch) -> None:
    run = AsyncMock(side_effect=[_result(Verdict.AC), _result(Verdict.WA)])
    monkeypatch.setattr(service, "run_docker_interaction", run)
    monkeypatch.setattr(service, "prepare_interactive_containers", AsyncMock())
    pool = AsyncMock()
    pool.acquire.side_effect = ["c1", "v1"]
    db = AsyncMock()

    result, _ = await _run(pool=pool, db=db, test_cases=[(1, b"a\n"), (2, b"b\n"), (3, b"c\n")])

    assert result is not None and result.classification.verdict == Verdict.WA
    assert run.await_count == 2
    assert db.insert_interactive_attempt.await_args.kwargs["test_case_ordinal"] == 2


@pytest.mark.asyncio
async def test_resource_usage_is_the_worst_across_executed_cases(monkeypatch) -> None:
    first = replace(_result(Verdict.AC), wall_time_ms=90, memory_kb=4096)
    second = replace(_result(Verdict.WA), wall_time_ms=30, memory_kb=8192)
    monkeypatch.setattr(service, "run_docker_interaction", AsyncMock(side_effect=[first, second]))
    monkeypatch.setattr(service, "prepare_interactive_containers", AsyncMock())
    pool = AsyncMock()
    pool.acquire.side_effect = ["c1", "v1"]

    result, _ = await _run(pool=pool, db=AsyncMock(), test_cases=[(1, b"a\n"), (2, b"b\n")])

    assert result is not None
    assert result.classification.verdict == Verdict.WA
    assert result.wall_time_ms == 90
    assert result.memory_kb == 8192


@pytest.mark.asyncio
async def test_a_problem_without_test_cases_cannot_be_judged() -> None:
    with pytest.raises(CustomValidatorUnavailableError):
        await _run(pool=AsyncMock(), db=AsyncMock(), test_cases=[])
