#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Per-submission compilation and execution of active custom validators.

The validator is parametrized by test-case input: one container pair judges the
whole submission, replaying the conversation once per test case with that case's
input fed to the validator's stdin. The first case that does not end ``AC`` stops
the iteration and its verdict is the submission's verdict.
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import Literal

import docker

from autojudge.compiler import compile_submission
from autojudge.config import settings
from autojudge.db import DatabaseAccess
from autojudge.interactive_runner import (
    InteractiveAttemptResult,
    prepare_interactive_containers,
    run_docker_interaction,
)
from autojudge.interactive_verdict import InteractiveVerdict
from autojudge.pool import PoolManager
from autojudge.types import CompileResult, CustomValidatorDispatchState, ProblemLimits, SubmissionSource
from shared.enumerations import CustomValidatorCrashReason, Verdict
from shared.language_registry import LanguageConfig, get_language

# Extra wall-clock time granted to the outer safety timeout so the bridge's own
# watchdog path can terminate both processes and collect isolate diagnostics
# before the whole attempt is cancelled.
_WATCHDOG_GRACE_SECONDS = 30


@dataclass(frozen=True)
class PreparedCustomValidator:
    """Dispatch-time validator language and per-submission compilation."""

    language: LanguageConfig
    compile_result: CompileResult


class CustomValidatorUnavailableError(RuntimeError):
    """Raised when a configured validator has no active valid revision."""


def _limits_payload(limits: ProblemLimits) -> dict[str, int]:
    """Return the JSON-serializable effective limit fields for one language."""
    return {
        "time_limit_ms": limits.time_limit_ms,
        "memory_limit_kb": limits.memory_limit_kb,
        "pids_limit": limits.pids_limit,
        "output_limit_in_bytes": limits.output_limit_in_bytes or settings.OUTPUT_LIMIT_BYTES,
        "repetitions": limits.repetitions,
    }


def build_validator_environment(
    *,
    limits: ProblemLimits,
    user_language_id: str,
    per_language_limits: dict[str, ProblemLimits] | None = None,
) -> dict[str, str]:
    """Build environment variables exposed to the trusted validator process."""
    environment = {
        "PROBLEM_TIME_LIMIT": str(limits.time_limit_ms),
        "PROBLEM_OUTPUT_LIMIT": str(limits.output_limit_in_bytes or settings.OUTPUT_LIMIT_BYTES),
        "PROBLEM_MEMORY_LIMIT": str(limits.memory_limit_kb),
        "PROBLEM_PID_LIMIT": str(limits.pids_limit),
        "USER_LANGUAGE": user_language_id,
    }
    if per_language_limits is not None:
        environment["PER_LANGUAGE_LIMITS"] = json.dumps(
            {
                language_id: _limits_payload(language_limits)
                for language_id, language_limits in per_language_limits.items()
            },
            sort_keys=True,
        )
    return environment


async def prepare_custom_validator(
    *,
    domain: str,
    judgment_id: str,
    problem_id: str,
    db: DatabaseAccess,
    language_registry: dict[str, LanguageConfig],
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
) -> PreparedCustomValidator | None:
    """Load and compile the active revision before contestant compilation."""
    state = await db.get_custom_validator_dispatch_state(domain, problem_id)
    if not isinstance(state, CustomValidatorDispatchState):
        return None
    if not state.configured:
        return None
    if state.active is None:
        raise CustomValidatorUnavailableError("The configured custom validator has no active valid revision.")
    language = get_language(language_registry, state.active.language_id)
    compile_result = await compile_submission(
        SubmissionSource(judgment_id, f"validator-{judgment_id}", state.active.source_code),
        language,
        docker_client,
        executor,
    )
    return PreparedCustomValidator(language, compile_result)


class _ContainerPair:
    """One contestant/validator container pair, reused across test cases."""

    def __init__(
        self,
        *,
        pool_manager: PoolManager,
        contestant_language: LanguageConfig,
        validator_language: LanguageConfig,
        contestant_artifact: bytes,
        validator_artifact: bytes,
        docker_client: docker.DockerClient,
        executor: ThreadPoolExecutor,
    ) -> None:
        self._pool_manager = pool_manager
        self._contestant_language = contestant_language
        self._validator_language = validator_language
        self._contestant_artifact = contestant_artifact
        self._validator_artifact = validator_artifact
        self._docker_client = docker_client
        self._executor = executor
        self._pair: tuple[str, str] | None = None

    async def acquire(self) -> tuple[str, str]:
        """Return the prepared pair, acquiring and seeding one when missing."""
        if self._pair is not None:
            return self._pair
        contestant_container = await self._pool_manager.acquire(self._contestant_language.id)
        try:
            validator_container = await self._pool_manager.acquire(self._validator_language.id)
        except BaseException:
            with suppress(Exception):
                await self._pool_manager.release(contestant_container)
            raise
        self._pair = (contestant_container, validator_container)
        try:
            await prepare_interactive_containers(
                contestant_container_id=contestant_container,
                validator_container_id=validator_container,
                contestant_language=self._contestant_language,
                validator_language=self._validator_language,
                contestant_artifact=self._contestant_artifact,
                validator_artifact=self._validator_artifact,
                docker_client=self._docker_client,
                executor=self._executor,
            )
        except BaseException:
            await self.discard()
            raise
        return self._pair

    async def discard(self) -> None:
        """Release the pair so the next case starts on fresh containers."""
        pair, self._pair = self._pair, None
        if pair is None:
            return
        for container_id in pair:
            with suppress(Exception):
                await self._pool_manager.release(container_id)


def _crash_result(exc: BaseException) -> InteractiveAttemptResult:
    """Synthesize a retryable failure for an attempt whose bridge never ran."""
    message = str(exc).encode("utf-8", errors="replace")[:16_384]
    crash_reason = (
        CustomValidatorCrashReason.WATCHDOG if isinstance(exc, TimeoutError) else CustomValidatorCrashReason.STARTUP
    )
    # The bridge never ran, so there is no conversation to record.
    return InteractiveAttemptResult(
        classification=InteractiveVerdict(None, True),
        contestant_exit_code=None,
        contestant_signal=None,
        validator_exit_code=None,
        validator_signal=None,
        transcript=None,
        contestant_stderr_excerpt=b"",
        validator_stderr_excerpt=message,
        contestant_output_bytes=0,
        crash_reason=crash_reason,
    )


def _peak(current: int | None, candidate: int | None) -> int | None:
    """Return the larger of two optional resource readings."""
    if current is None:
        return candidate
    if candidate is None:
        return current
    return max(current, candidate)


async def _judge_test_case(
    *,
    domain: str,
    judgment_id: str,
    attempt_target: Literal["submission", "solution_test"],
    attempt_token: str | None,
    ordinal: int,
    testcase_input: bytes,
    containers: _ContainerPair,
    contestant_language: LanguageConfig,
    validator_language: LanguageConfig,
    limits: ProblemLimits,
    db: DatabaseAccess,
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
    validator_environment: dict[str, str],
) -> InteractiveAttemptResult:
    """Run one test case, retrying once when the validator does not exit cleanly."""
    result = _crash_result(RuntimeError("No interactive attempt ran."))
    for attempt_number in (1, 2):
        try:
            contestant_container, validator_container = await containers.acquire()
            result = await asyncio.wait_for(
                run_docker_interaction(
                    contestant_container_id=contestant_container,
                    validator_container_id=validator_container,
                    contestant_language=contestant_language,
                    validator_language=validator_language,
                    testcase_input=testcase_input,
                    limits=limits,
                    docker_client=docker_client,
                    executor=executor,
                    output_limit_bytes=limits.output_limit_in_bytes or settings.OUTPUT_LIMIT_BYTES,
                    watchdog_seconds=settings.CUSTOM_VALIDATOR_WATCHDOG_SECONDS,
                    validator_environment=validator_environment,
                ),
                timeout=settings.CUSTOM_VALIDATOR_WATCHDOG_SECONDS + _WATCHDOG_GRACE_SECONDS,
            )
        except Exception as exc:
            result = _crash_result(exc)
        # Attempt 1 clears the rows of any earlier case, so a judgment only ever
        # retains the attempts of the last executed case.
        await db.insert_interactive_attempt(
            domain=domain,
            owner_id=judgment_id,
            attempt_number=attempt_number,
            test_case_ordinal=ordinal,
            result=result,
            attempt_target=attempt_target,
            attempt_token=attempt_token,
        )
        if not result.classification.retryable_validator_failure:
            break
        # An unclean exit leaves both containers killed, so a retry needs a fresh pair.
        await containers.discard()
    return result


async def run_custom_validator_submission(
    *,
    domain: str,
    judgment_id: str,
    problem_id: str,
    contestant_language: LanguageConfig,
    contestant_artifact: bytes,
    limits: ProblemLimits,
    test_cases: list[tuple[int, bytes]],
    db: DatabaseAccess,
    pool_manager: PoolManager,
    language_registry: dict[str, LanguageConfig],
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
    prepared: PreparedCustomValidator | None,
    user_language_id: str,
    per_language_limits: dict[str, ProblemLimits] | None = None,
    attempt_target: Literal["submission", "solution_test"] = "submission",
    attempt_token: str | None = None,
) -> tuple[InteractiveAttemptResult | None, CompileResult | None]:
    """Replay the validator once per test case until one does not end ``AC``.

    Args:
        test_cases: Ordered ``(ordinal, input_bytes)`` cases parametrizing the
            validator. Must not be empty; callers gate that before dispatching.
        attempt_target: Whether the recorded attempts belong to a real judgment
            or to a non-scoring solution-test run. Independent of ``domain``,
            which selects the contest-vs-arena validator schema.
        attempt_token: Claim stamped by this attempt's dispatch, so a transcript
            is only recorded while this attempt still owns the judgment.

    Returns:
        The last executed case's result, carrying the worst wall time and memory
        seen across the executed cases, plus the validator's compilation.

    Raises:
        CustomValidatorUnavailableError: If the problem has no test cases.
    """
    if prepared is None:
        return None, None
    validator_language = prepared.language
    validator_compile = prepared.compile_result
    if not validator_compile.success or validator_compile.artifact_data is None:
        return None, validator_compile
    if not test_cases:
        raise CustomValidatorUnavailableError("A custom-validator problem must have at least one test case.")

    containers = _ContainerPair(
        pool_manager=pool_manager,
        contestant_language=contestant_language,
        validator_language=validator_language,
        contestant_artifact=contestant_artifact,
        validator_artifact=validator_compile.artifact_data,
        docker_client=docker_client,
        executor=executor,
    )
    validator_environment = build_validator_environment(
        limits=limits,
        user_language_id=user_language_id,
        per_language_limits=per_language_limits,
    )
    result = _crash_result(RuntimeError("No interactive attempt ran."))
    worst_wall_time_ms: int | None = None
    worst_memory_kb: int | None = None
    try:
        for ordinal, testcase_input in test_cases:
            result = await _judge_test_case(
                domain=domain,
                judgment_id=judgment_id,
                attempt_target=attempt_target,
                attempt_token=attempt_token,
                ordinal=ordinal,
                testcase_input=testcase_input,
                containers=containers,
                contestant_language=contestant_language,
                validator_language=validator_language,
                limits=limits,
                db=db,
                docker_client=docker_client,
                executor=executor,
                validator_environment=validator_environment,
            )
            worst_wall_time_ms = _peak(worst_wall_time_ms, result.wall_time_ms)
            worst_memory_kb = _peak(worst_memory_kb, result.memory_kb)
            if result.classification.verdict != Verdict.AC:
                break
    finally:
        await containers.discard()

    return (
        replace(result, wall_time_ms=worst_wall_time_ms, memory_kb=worst_memory_kb),
        validator_compile,
    )
