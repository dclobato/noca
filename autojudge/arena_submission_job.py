#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena submission job processing pipeline."""

from __future__ import annotations

import json
import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

import docker
import valkey.asyncio as aiovalkey

from autojudge.compiler import compile_submission
from autojudge.db import DatabaseAccess, QueuedArenaSubmission
from autojudge.metrics import SUBMISSION_DURATION_SECONDS, TEST_CASES_RUN_TOTAL, VERDICTS_TOTAL
from autojudge.pool import PoolExhaustedError, PoolManager, PoolShutdownError
from autojudge.queue_ops import publish_arena_verdict
from autojudge.runtime_utils import is_recoverable_isolate_runtime_error
from autojudge.submission_job import _run_repeated_test_case
from autojudge.types import IsolateError, SubmissionSource
from autojudge.verdict import CaseResult, aggregate_verdict, worst_resource_usage
from shared.enumerations import Verdict
from shared.language_registry import LanguageConfig, get_language
from shared.queue_schema import ArenaVerdictEvent

logger = logging.getLogger(__name__)

Valkey_Client = aiovalkey.Valkey


async def process_arena_submission_job(
    *,
    submission: QueuedArenaSubmission,
    db: DatabaseAccess,
    valkey: Valkey_Client,
    pool_manager: PoolManager,
    language_registry: dict[str, LanguageConfig],
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
    worker_id: str,
) -> None:
    """Execute compile, fail-fast tests, and Arena-specific persistence."""
    judgment_id = submission.judgment_id
    submission_id = submission.submission_id

    async def _publish(verdict: Verdict) -> None:
        """Publish the finalized Arena verdict for the live feed (best-effort)."""
        await publish_arena_verdict(
            valkey,
            ArenaVerdictEvent(
                submission_id=submission_id,
                judgment_id=judgment_id,
                verdict=verdict.value,
            ),
        )

    await db.set_arena_judgment_dispatched(judgment_id, worker_id)
    try:
        language = get_language(language_registry, submission.language_id)
    except KeyError as exc:
        await db.set_arena_judgment_failed(judgment_id, str(exc))
        return

    compile_result = await compile_submission(
        SubmissionSource(
            judgment_id=judgment_id,
            submission_id=submission_id,
            source_code=submission.source_code,
        ),
        language,
        docker_client,
        executor,
    )
    if not compile_result.success:
        VERDICTS_TOTAL.labels(verdict=Verdict.CE.value, language_id=submission.language_id).inc()
        await db.set_arena_judgment_done(
            submission,
            verdict=Verdict.CE,
            compile_log=compile_result.compile_log,
        )
        await _publish(Verdict.CE)
        return

    await db.set_arena_judgment_judging(judgment_id)
    try:
        container_id: str | None = await pool_manager.acquire(submission.language_id)
    except (PoolExhaustedError, PoolShutdownError) as exc:
        await db.set_arena_judgment_failed(judgment_id, str(exc))
        return

    try:
        case_results: list[CaseResult] = []
        start_judge = time.monotonic()

        for test_case in submission.test_cases:
            try:
                if container_id is None:
                    raise RuntimeError("Arena submission container was not available for test execution")
                repeated_result = await _run_repeated_test_case(
                    container_id=container_id,
                    language=language,
                    limits=submission.limits,
                    artifact_data=compile_result.artifact_data or b"",
                    input_data=test_case.input_data,
                    expected_output=test_case.expected_output,
                    docker_client=docker_client,
                    executor=executor,
                )
            except IsolateError as exc:
                if not is_recoverable_isolate_runtime_error(exc):
                    raise

                logger.warning(
                    "Recoverable isolate runtime failure while judging Arena submission; "
                    "recycling container and retrying current test case once."
                )
                logger.warning(
                    json.dumps(
                        {
                            "submission_id": submission_id,
                            "judgment_id": judgment_id,
                            "problem_id": submission.problem_id,
                            "language_id": submission.language_id,
                            "container_id": container_id[:12] if container_id else None,
                            "test_case_ordinal": test_case.ordinal,
                            "error": str(exc),
                        },
                        indent=2,
                    )
                )
                bad_container_id = container_id
                if bad_container_id is None:
                    raise RuntimeError("Arena submission container disappeared during retry handling") from exc
                container_id = None
                with suppress(Exception):
                    await pool_manager.release(bad_container_id)
                container_id = await pool_manager.acquire(submission.language_id)
                repeated_result = await _run_repeated_test_case(
                    container_id=container_id,
                    language=language,
                    limits=submission.limits,
                    artifact_data=compile_result.artifact_data or b"",
                    input_data=test_case.input_data,
                    expected_output=test_case.expected_output,
                    docker_client=docker_client,
                    executor=executor,
                )

            TEST_CASES_RUN_TOTAL.labels(language_id=submission.language_id).inc()
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
            if repeated_result.verdict != Verdict.AC:
                await db.insert_arena_test_result(
                    judgment_id=judgment_id,
                    test_case_id=test_case.test_case_id,
                    verdict=repeated_result.verdict,
                    wall_time_ms=repeated_result.total_wall_time_ms,
                    memory_kb=repeated_result.peak_memory_kb,
                    exit_code=repeated_result.exit_code,
                    exit_signal=repeated_result.exit_signal,
                    stdout_excerpt=repeated_result.stdout_excerpt,
                    stderr_excerpt=repeated_result.stderr_excerpt,
                )
                break

        total_judge_ms = int((time.monotonic() - start_judge) * 1000)
        final_verdict = aggregate_verdict(case_results)
        resource_peak = worst_resource_usage(case_results)
        VERDICTS_TOTAL.labels(verdict=final_verdict.value, language_id=submission.language_id).inc()
        SUBMISSION_DURATION_SECONDS.labels(language_id=submission.language_id).observe(total_judge_ms / 1000)

        await db.set_arena_judgment_done(
            submission,
            verdict=final_verdict,
            compile_log=compile_result.compile_log or None,
            max_wall_time_ms=resource_peak["peak_wall_time_ms"],
            max_memory_kb=resource_peak["peak_memory_kb"],
            max_output_bytes=resource_peak["peak_output_bytes"],
        )
        await _publish(final_verdict)
    except (PoolExhaustedError, PoolShutdownError) as exc:
        await db.set_arena_judgment_failed(judgment_id, str(exc))
        return
    except Exception as exc:
        await db.set_arena_judgment_failed(judgment_id, f"Internal judge error: {exc}\n\n{traceback.format_exc()}")
        raise
    finally:
        if container_id is not None:
            with suppress(Exception):
                await pool_manager.release(container_id)
