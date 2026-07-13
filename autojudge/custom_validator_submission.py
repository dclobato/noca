#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Per-submission compilation and execution of active custom validators."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass

import docker

from autojudge.compiler import compile_submission
from autojudge.config import settings
from autojudge.db import DatabaseAccess
from autojudge.interactive_runner import InteractiveAttemptResult, run_docker_interaction
from autojudge.interactive_verdict import InteractiveVerdict
from autojudge.pool import PoolManager
from autojudge.types import CompileResult, CustomValidatorDispatchState, ProblemLimits, SubmissionSource
from shared.enumerations import CustomValidatorCrashReason
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


async def run_custom_validator_submission(
    *,
    domain: str,
    judgment_id: str,
    problem_id: str,
    contestant_language: LanguageConfig,
    contestant_artifact: bytes,
    limits: ProblemLimits,
    db: DatabaseAccess,
    pool_manager: PoolManager,
    language_registry: dict[str, LanguageConfig],
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
    prepared: PreparedCustomValidator | None,
) -> tuple[InteractiveAttemptResult | None, CompileResult | None]:
    """Compile the active revision and run up to two fresh interactions."""
    if prepared is None:
        return None, None
    validator_language = prepared.language
    validator_compile = prepared.compile_result
    if not validator_compile.success or validator_compile.artifact_data is None:
        return None, validator_compile

    result: InteractiveAttemptResult | None = None
    for attempt_number in (1, 2):
        contestant_container: str | None = None
        validator_container: str | None = None
        try:
            contestant_container = await pool_manager.acquire(contestant_language.id)
            validator_container = await pool_manager.acquire(validator_language.id)
            try:
                result = await asyncio.wait_for(
                    run_docker_interaction(
                        contestant_container_id=contestant_container,
                        validator_container_id=validator_container,
                        contestant_language=contestant_language,
                        validator_language=validator_language,
                        contestant_artifact=contestant_artifact,
                        validator_artifact=validator_compile.artifact_data,
                        limits=limits,
                        docker_client=docker_client,
                        executor=executor,
                        output_limit_bytes=limits.output_limit_in_bytes or settings.OUTPUT_LIMIT_BYTES,
                        watchdog_seconds=settings.CUSTOM_VALIDATOR_WATCHDOG_SECONDS,
                    ),
                    timeout=settings.CUSTOM_VALIDATOR_WATCHDOG_SECONDS + _WATCHDOG_GRACE_SECONDS,
                )
            except Exception as exc:
                message = str(exc).encode("utf-8", errors="replace")[:16_384]
                crash_reason = (
                    CustomValidatorCrashReason.WATCHDOG
                    if isinstance(exc, TimeoutError)
                    else CustomValidatorCrashReason.STARTUP
                )
                # The bridge never ran, so there is no conversation to record.
                result = InteractiveAttemptResult(
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
            await db.insert_interactive_attempt(
                domain=domain,
                judgment_id=judgment_id,
                attempt_number=attempt_number,
                result=result,
            )
        finally:
            for container_id in (contestant_container, validator_container):
                if container_id is not None:
                    with suppress(Exception):
                        await pool_manager.release(container_id)
        if not result.classification.retryable_validator_failure:
            break
    return result, validator_compile
