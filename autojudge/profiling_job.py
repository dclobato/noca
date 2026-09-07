#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Auto-Limit profiling job processing pipeline.

Provides:
- profiling_hard_limits()   — compute the hard infrastructure caps for profiling runs
- process_profiling_job()   — full compile → run → persist limits pipeline for one profiling run
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

import docker

from autojudge.compiler import compile_submission
from autojudge.config import settings
from autojudge.db import DatabaseAccess
from autojudge.languages import LanguageConfig
from autojudge.metrics import PROFILING_DURATION_SECONDS
from autojudge.pool import PoolExhaustedError, PoolManager, PoolShutdownError
from autojudge.runtime_utils import is_recoverable_isolate_runtime_error
from autojudge.submission_job import _load_test_cases, _run_repeated_test_case
from autojudge.types import ProblemLimits, QueuedProfilingRun, SubmissionSource
from shared.enumerations import Verdict
from shared.language_registry import get_language

logger = logging.getLogger(__name__)


def profiling_hard_limits() -> ProblemLimits:
    """
    Return the hard infrastructure caps used while profiling.

    Returns:
        ProblemLimits with caps derived from settings.
    """
    cpu_cap_ms = int(
        min(
            settings.PROFILING_MAX_CPU_TIME_SEC,
            settings.PROFILING_MAX_WALL_TIME_SEC / settings.ISOLATE_WALL_TIME_MULTIPLIER,
        )
        * 1000
    )
    return ProblemLimits(
        time_limit_ms=max(1, cpu_cap_ms),
        memory_limit_kb=settings.PROFILING_MAX_MEMORY_MB * 1024,
        pids_limit=settings.PROFILING_MAX_PIDS,
        output_limit_in_bytes=settings.PROFILING_MAX_OUTPUT_BYTES,
        repetitions=1,
    )


async def process_profiling_job(
    profiling_run: QueuedProfilingRun,
    db: DatabaseAccess,
    pool_manager: PoolManager,
    language_registry: dict[str, LanguageConfig],
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
    worker_id: str,
    attempt_token: str,
) -> None:
    """
    Execute profiling for one problem/language reference implementation.

    Args:
        profiling_run: Profiling run payload loaded from the database.
        db: Open worker database accessor.
        pool_manager: Container pool manager.
        language_registry: Active language registry.
        docker_client: Synchronous Docker client.
        executor: ThreadPoolExecutor for Docker SDK calls.
        worker_id: Stable worker identity string.
        attempt_token: Attempt-scoped claim; every write below is fenced on it,
            so an attempt whose run was taken over writes nothing.

    Raises:
        JudgmentOwnershipLost: If another attempt claims the run mid-execution.
    """
    from autojudge.runner import IsolateError  # avoid circular at module level

    container_id: str | None = None
    profiling_run_id = profiling_run.profiling_run_id

    await db.set_profiling_dispatched(profiling_run_id, worker_id, attempt_token)

    try:
        language = get_language(language_registry, profiling_run.language_id)
    except KeyError as exc:
        await db.set_profiling_failed(profiling_run_id, str(exc), attempt_token=attempt_token)
        return

    # Resolved once, here, and used for every decision below: the limits this run
    # measures under, the mean it divides by, and the count it persists. Reading
    # profiling_run.repetitions instead would read the row as it was loaded --
    # before this run stamped it -- and dividing by a registry default read a
    # second time would silently disagree with the stored row if that default is
    # edited between the two reads.
    repetition_count = language.profiling_repetitions_default

    compile_result = await compile_submission(
        SubmissionSource(
            judgment_id=profiling_run_id,
            submission_id=profiling_run_id,
            source_code=profiling_run.source_code,
        ),
        language,
        docker_client,
        executor,
    )
    if not compile_result.success:
        await db.set_profiling_failed(
            profiling_run_id,
            "Reference implementation failed to compile.",
            compile_log=compile_result.compile_log,
            attempt_token=attempt_token,
        )
        return

    await db.set_profiling_running(profiling_run_id, attempt_token, repetition_count)

    try:
        profile_limits = profiling_hard_limits()
        # Profiling needs the cap enforced twice over, and the two are different
        # numbers. Pairing it with the repetition count sets the *aggregate*
        # budget at cap x repetitions, so every repetition gets a full cap's
        # worth instead of a tenth of one -- before the time limit became a
        # per-run value, ten repetitions squeezed into a single cap and a
        # legitimately slow reference implementation failed on the infrastructure
        # ceiling rather than on its own merits. But this cap is not a problem's
        # time limit, where one slow run borrowing from a fast one is the whole
        # design; it exists to bound a *single* execution. So the same value is
        # passed to _run_repeated_test_case as per_run_ceiling_ms, or a hung
        # reference implementation would spend the entire aggregate in its first
        # run and hold a judge container for all of it.
        effective_limits = ProblemLimits(
            time_limit_ms=profile_limits.time_limit_ms,
            memory_limit_kb=profile_limits.memory_limit_kb,
            pids_limit=profile_limits.pids_limit,
            output_limit_in_bytes=profile_limits.output_limit_in_bytes,
            repetitions=repetition_count,
        )
        test_cases = _load_test_cases(profiling_run.problem_id)
    except (LookupError, FileNotFoundError, ValueError) as exc:
        await db.set_profiling_failed(
            profiling_run_id,
            str(exc),
            compile_log=compile_result.compile_log,
            attempt_token=attempt_token,
        )
        return

    test_case_ids = await db.get_test_case_id_map(profiling_run.problem_id)
    if len(test_case_ids) < len(test_cases):
        await db.set_profiling_failed(
            profiling_run_id,
            f"Filesystem/DB mismatch for problem '{profiling_run.problem_id}'.",
            compile_log=compile_result.compile_log,
            attempt_token=attempt_token,
        )
        return

    try:
        container_id = await pool_manager.acquire(profiling_run.language_id)
    except (PoolExhaustedError, PoolShutdownError) as exc:
        await db.set_profiling_failed(
            profiling_run_id,
            str(exc),
            compile_log=compile_result.compile_log,
            attempt_token=attempt_token,
        )
        return

    profiling_start = time.monotonic()
    try:
        observed_time_ms = 0
        observed_memory_kb = 0
        observed_output_bytes = 0
        observed_pids = 0
        fallback_peak_pids = 16

        for ordinal, (input_data, expected_output) in enumerate(test_cases, start=1):
            try:
                repeated_result = await _run_repeated_test_case(
                    container_id=container_id,
                    language=language,
                    limits=effective_limits,
                    artifact_data=compile_result.artifact_data or b"",
                    input_data=input_data,
                    expected_output=expected_output,
                    docker_client=docker_client,
                    executor=executor,
                    per_run_ceiling_ms=profile_limits.time_limit_ms,
                )
            except IsolateError as exc:
                if not is_recoverable_isolate_runtime_error(exc):
                    raise

                logger.warning(
                    "Recoverable isolate runtime failure while profiling; "
                    "recycling container and retrying current test case once."
                )
                logger.warning(
                    json.dumps(
                        {
                            "profiling_run_id": profiling_run_id,
                            "problem_id": profiling_run.problem_id,
                            "language_id": profiling_run.language_id,
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
                container_id = await pool_manager.acquire(profiling_run.language_id)
                repeated_result = await _run_repeated_test_case(
                    container_id=container_id,
                    language=language,
                    limits=effective_limits,
                    artifact_data=compile_result.artifact_data or b"",
                    input_data=input_data,
                    expected_output=expected_output,
                    docker_client=docker_client,
                    executor=executor,
                    per_run_ceiling_ms=profile_limits.time_limit_ms,
                )

            observed_case_pids = (
                repeated_result.peak_pids if repeated_result.peak_pids is not None else fallback_peak_pids
            )
            await db.insert_profiling_case_result(
                profiling_run_id=profiling_run_id,
                test_case_id=test_case_ids[ordinal],
                ordinal=ordinal,
                verdict=repeated_result.verdict,
                total_wall_time_ms=repeated_result.total_wall_time_ms,
                peak_memory_kb=repeated_result.peak_memory_kb,
                peak_output_bytes=repeated_result.peak_output_bytes,
                peak_pids=observed_case_pids,
                exit_code=repeated_result.exit_code,
                attempt_token=attempt_token,
            )
            if repeated_result.total_wall_time_ms is not None:
                observed_time_ms = max(observed_time_ms, repeated_result.total_wall_time_ms)
            if repeated_result.peak_memory_kb is not None:
                observed_memory_kb = max(observed_memory_kb, repeated_result.peak_memory_kb)
            if repeated_result.peak_output_bytes is not None:
                observed_output_bytes = max(observed_output_bytes, repeated_result.peak_output_bytes)
            observed_pids = max(observed_pids, observed_case_pids)

            if repeated_result.verdict != Verdict.AC:
                await db.set_profiling_failed(
                    profiling_run_id,
                    (
                        "Reference implementation failed on test case "
                        f"{ordinal} with verdict {repeated_result.verdict.value}."
                    ),
                    compile_log=compile_result.compile_log,
                    attempt_token=attempt_token,
                )
                return

        profiled_limits = db.compute_profiled_limits(
            safety_factor=profiling_run.safety_factor,
            time_limit_ms=observed_time_ms,
            repetitions=repetition_count,
            memory_limit_kb=observed_memory_kb,
            pids_limit=observed_pids,
            output_limit_in_bytes=observed_output_bytes,
            pids_floor=language.profiled_pids_floor,
        )
        await db.set_profiling_done(
            profiling_run_id,
            profiled_limits,
            repetition_count,
            attempt_token=attempt_token,
            compile_log=compile_result.compile_log,
        )
    except (PoolExhaustedError, PoolShutdownError) as exc:
        await db.set_profiling_failed(
            profiling_run_id,
            str(exc),
            compile_log=compile_result.compile_log,
            attempt_token=attempt_token,
        )
        return
    finally:
        PROFILING_DURATION_SECONDS.labels(language_id=profiling_run.language_id).observe(
            time.monotonic() - profiling_start
        )
        if container_id is not None:
            with suppress(Exception):
                await pool_manager.release(container_id)
