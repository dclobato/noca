#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Non-scoring solution-test job processing pipeline.

A judge or admin runs a candidate solution against a contest problem's real
compiler, sandbox, limits, test cases, and custom validator, and sees a verdict —
with zero effect on the competition.

The signature deliberately takes **no** ``valkey`` handle, following the
profiling pipeline: publishing a verdict event, invalidating the scoreboard
cache, or creating a balloon task is then structurally impossible here rather
than merely test-enforced.

Provides:
- process_solution_test_job() — full compile → run → persist verdict pipeline
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

import docker

from autojudge.compiler import compile_submission
from autojudge.custom_validator_submission import (
    PreparedCustomValidator,
    prepare_custom_validator,
    run_custom_validator_submission,
)
from autojudge.db import DatabaseAccess
from autojudge.languages import LanguageConfig
from autojudge.metrics import SOLUTION_TEST_DURATION_SECONDS, SOLUTION_TEST_VERDICTS_TOTAL
from autojudge.pool import PoolExhaustedError, PoolManager, PoolShutdownError
from autojudge.runtime_utils import is_recoverable_isolate_runtime_error
from autojudge.submission_job import _load_test_case_inputs, _load_test_cases, _run_repeated_test_case
from autojudge.types import ProblemLimits, QueuedSolutionTestRun, SubmissionSource
from autojudge.verdict import CaseResult, aggregate_verdict, worst_resource_usage
from shared.enumerations import Verdict
from shared.language_registry import get_language

logger = logging.getLogger(__name__)

_CASE_DETAIL_MAX_BYTES = 10 * 1024
_TRUNCATION_MARKER = b"\n[truncated at 10 KB]"


def _bounded_case_detail(value: bytes) -> bytes:
    """Bound one case-detail value to 10 KiB, including its truncation marker."""
    if len(value) <= _CASE_DETAIL_MAX_BYTES:
        return value
    content_limit = _CASE_DETAIL_MAX_BYTES - len(_TRUNCATION_MARKER)
    return value[:content_limit] + _TRUNCATION_MARKER


async def _run_interactive(
    *,
    solution_test_run: QueuedSolutionTestRun,
    db: DatabaseAccess,
    prepared: PreparedCustomValidator,
    language: LanguageConfig,
    limits: ProblemLimits,
    artifact_data: bytes,
    compile_log: str | None,
    pool_manager: PoolManager,
    language_registry: dict[str, LanguageConfig],
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
    attempt_token: str,
) -> None:
    """Replay the custom validator once per case and persist the run's verdict.

    The validator replay inherently stops at the first case that does not end
    ``AC``, and that case's verdict is the run's verdict — this is
    ``run_custom_validator_submission``'s contract, not a choice made here.
    """
    run_id = solution_test_run.solution_test_run_id
    try:
        per_language_limits = await db.get_problem_effective_limits_by_language(solution_test_run.problem_id)
        interactive_inputs = _load_test_case_inputs(solution_test_run.problem_id)
    except (FileNotFoundError, LookupError, ValueError) as exc:
        await db.set_solution_test_failed(run_id, str(exc), compile_log=compile_log, attempt_token=attempt_token)
        return

    started = time.monotonic()
    try:
        interactive_result, _ = await run_custom_validator_submission(
            domain="contest",
            judgment_id=run_id,
            problem_id=solution_test_run.problem_id,
            contestant_language=language,
            contestant_artifact=artifact_data,
            limits=limits,
            test_cases=interactive_inputs,
            db=db,
            pool_manager=pool_manager,
            language_registry=language_registry,
            docker_client=docker_client,
            executor=executor,
            prepared=prepared,
            user_language_id=solution_test_run.language_id,
            per_language_limits=per_language_limits,
            attempt_target="solution_test",
            attempt_token=attempt_token,
        )
    finally:
        SOLUTION_TEST_DURATION_SECONDS.labels(language_id=solution_test_run.language_id).observe(
            time.monotonic() - started
        )

    if interactive_result is None:
        await db.set_solution_test_failed(
            run_id,
            "The custom validator produced no result.",
            compile_log=compile_log,
            attempt_token=attempt_token,
        )
        return

    verdict = interactive_result.classification.verdict
    if verdict is None:
        await db.set_solution_test_failed(
            run_id,
            "Custom validator failed twice without a clean exit.",
            compile_log=compile_log,
            attempt_token=attempt_token,
        )
        return

    SOLUTION_TEST_VERDICTS_TOTAL.labels(verdict=verdict.value, language_id=solution_test_run.language_id).inc()
    await db.set_solution_test_done(
        run_id,
        attempt_token=attempt_token,
        verdict=verdict,
        compile_log=compile_log,
        max_wall_time_ms=interactive_result.wall_time_ms,
        max_memory_kb=interactive_result.memory_kb,
    )


async def process_solution_test_job(
    solution_test_run: QueuedSolutionTestRun,
    db: DatabaseAccess,
    pool_manager: PoolManager,
    language_registry: dict[str, LanguageConfig],
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
    worker_id: str,
    attempt_token: str,
) -> None:
    """
    Execute one non-scoring solution test end to end.

    Ordinary problems run *every* test case and aggregate with
    ``aggregate_verdict``, exactly like a real submission — this is where the
    pipeline differs from profiling, which stops at the first non-``AC`` case.

    Args:
        solution_test_run: Solution-test run payload loaded from the database.
        db: Open worker database accessor.
        pool_manager: Container pool manager.
        language_registry: Active language registry.
        docker_client: Synchronous Docker client.
        executor: ThreadPoolExecutor for Docker SDK calls.
        worker_id: Stable worker identity string.
        attempt_token: Attempt-scoped claim; every write below is fenced on it,
            so an attempt whose run was taken over writes nothing.
    """
    from autojudge.runner import IsolateError  # avoid circular at module level

    container_id: str | None = None
    run_id = solution_test_run.solution_test_run_id

    await db.set_solution_test_dispatched(run_id, worker_id, attempt_token)

    try:
        language = get_language(language_registry, solution_test_run.language_id)
    except KeyError as exc:
        await db.set_solution_test_failed(run_id, str(exc), attempt_token=attempt_token)
        return

    try:
        limits = await db.get_problem_limits(solution_test_run.problem_id, solution_test_run.language_id)
        prepared_validator = await prepare_custom_validator(
            domain="contest",
            judgment_id=run_id,
            problem_id=solution_test_run.problem_id,
            db=db,
            language_registry=language_registry,
            docker_client=docker_client,
            executor=executor,
        )
    except Exception as exc:
        await db.set_solution_test_failed(run_id, f"Custom validator unavailable: {exc}", attempt_token=attempt_token)
        return

    if prepared_validator is not None and (
        not prepared_validator.compile_result.success or prepared_validator.compile_result.artifact_data is None
    ):
        await db.set_solution_test_failed(
            run_id,
            f"Custom validator compilation failed: {prepared_validator.compile_result.compile_log}",
            attempt_token=attempt_token,
        )
        return

    compile_result = await compile_submission(
        SubmissionSource(judgment_id=run_id, submission_id=run_id, source_code=solution_test_run.source_code),
        language,
        docker_client,
        executor,
    )
    if not compile_result.success:
        SOLUTION_TEST_VERDICTS_TOTAL.labels(verdict=Verdict.CE.value, language_id=solution_test_run.language_id).inc()
        await db.set_solution_test_done(
            run_id,
            attempt_token=attempt_token,
            verdict=Verdict.CE,
            compile_log=compile_result.compile_log,
        )
        return

    await db.set_solution_test_running(run_id, attempt_token)
    compile_log = compile_result.compile_log or None

    if prepared_validator is not None:
        await _run_interactive(
            solution_test_run=solution_test_run,
            db=db,
            prepared=prepared_validator,
            language=language,
            limits=limits,
            artifact_data=compile_result.artifact_data or b"",
            compile_log=compile_log,
            pool_manager=pool_manager,
            language_registry=language_registry,
            docker_client=docker_client,
            executor=executor,
            attempt_token=attempt_token,
        )
        return

    try:
        test_cases = _load_test_cases(solution_test_run.problem_id)
    except (FileNotFoundError, ValueError) as exc:
        await db.set_solution_test_failed(run_id, str(exc), compile_log=compile_log, attempt_token=attempt_token)
        return

    test_case_ids = await db.get_test_case_id_map(solution_test_run.problem_id)
    if len(test_case_ids) < len(test_cases):
        await db.set_solution_test_failed(
            run_id,
            f"Filesystem/DB mismatch: {len(test_cases)} test files on disk but only "
            f"{len(test_case_ids)} test_case rows in DB for problem '{solution_test_run.problem_id}'.",
            compile_log=compile_log,
        )
        return

    try:
        container_id = await pool_manager.acquire(solution_test_run.language_id)
    except (PoolExhaustedError, PoolShutdownError) as exc:
        await db.set_solution_test_failed(run_id, str(exc), compile_log=compile_log, attempt_token=attempt_token)
        return

    started = time.monotonic()
    try:
        case_results: list[CaseResult] = []

        for ordinal, (input_data, expected_output) in enumerate(test_cases, start=1):
            try:
                repeated_result = await _run_repeated_test_case(
                    container_id=container_id,
                    language=language,
                    limits=limits,
                    artifact_data=compile_result.artifact_data or b"",
                    input_data=input_data,
                    expected_output=expected_output,
                    docker_client=docker_client,
                    executor=executor,
                )
            except IsolateError as exc:
                if not is_recoverable_isolate_runtime_error(exc):
                    raise

                logger.warning(
                    "Recoverable isolate runtime failure while running a solution test; "
                    "recycling container and retrying current test case once."
                )
                logger.warning(
                    json.dumps(
                        {
                            "solution_test_run_id": run_id,
                            "problem_id": solution_test_run.problem_id,
                            "language_id": solution_test_run.language_id,
                            "container_id": container_id[:12] if container_id else None,
                            "test_case_ordinal": ordinal,
                            "error": str(exc),
                        },
                        indent=2,
                    )
                )
                bad_container_id = container_id
                container_id = None
                with suppress(Exception):
                    await pool_manager.release(bad_container_id)
                container_id = await pool_manager.acquire(solution_test_run.language_id)
                repeated_result = await _run_repeated_test_case(
                    container_id=container_id,
                    language=language,
                    limits=limits,
                    artifact_data=compile_result.artifact_data or b"",
                    input_data=input_data,
                    expected_output=expected_output,
                    docker_client=docker_client,
                    executor=executor,
                )

            await db.insert_solution_test_case_result(
                solution_test_run_id=run_id,
                test_case_id=test_case_ids.get(ordinal),
                ordinal=ordinal,
                verdict=repeated_result.verdict,
                wall_time_ms=repeated_result.total_wall_time_ms,
                memory_kb=repeated_result.peak_memory_kb,
                output_bytes=repeated_result.peak_output_bytes,
                exit_code=repeated_result.exit_code,
                exit_signal=repeated_result.exit_signal,
                input_excerpt=_bounded_case_detail(input_data),
                expected_output_excerpt=_bounded_case_detail(expected_output),
                stdout_excerpt=_bounded_case_detail(repeated_result.stdout_excerpt),
                stderr_excerpt=repeated_result.stderr_excerpt,
                attempt_token=attempt_token,
            )
            case_results.append(
                CaseResult(
                    verdict=repeated_result.verdict,
                    wall_time_ms=repeated_result.total_wall_time_ms,
                    memory_kb=repeated_result.peak_memory_kb,
                    output_bytes=repeated_result.peak_output_bytes,
                    peak_pids=repeated_result.peak_pids,
                    exit_code=repeated_result.exit_code,
                    exit_signal=repeated_result.exit_signal,
                    stdout_excerpt=repeated_result.stdout_excerpt,
                    stderr_excerpt=repeated_result.stderr_excerpt,
                )
            )

        final_verdict = aggregate_verdict(case_results)
        resource_peak = worst_resource_usage(case_results)
        SOLUTION_TEST_VERDICTS_TOTAL.labels(
            verdict=final_verdict.value, language_id=solution_test_run.language_id
        ).inc()
        await db.set_solution_test_done(
            run_id,
            attempt_token=attempt_token,
            verdict=final_verdict,
            compile_log=compile_log,
            max_wall_time_ms=resource_peak["peak_wall_time_ms"],
            max_memory_kb=resource_peak["peak_memory_kb"],
        )
    except (PoolExhaustedError, PoolShutdownError) as exc:
        await db.set_solution_test_failed(run_id, str(exc), compile_log=compile_log, attempt_token=attempt_token)
        return
    finally:
        SOLUTION_TEST_DURATION_SECONDS.labels(language_id=solution_test_run.language_id).observe(
            time.monotonic() - started
        )
        if container_id is not None:
            with suppress(Exception):
                await pool_manager.release(container_id)
