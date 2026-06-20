#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Run phase of the judge pipeline.

Public API
----------
    run_test_case(container_id, language, limits, artifact_data,
                  input_data, expected_output, docker_client, executor) -> RunResult

Async. All Docker SDK calls are dispatched to a ThreadPoolExecutor via
run_in_executor, keeping the asyncio event loop free.

Re-exports
----------
Types that callers previously imported from this module are re-exported here
for backward compatibility. Prefer importing from autojudge.types directly.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

import docker
import docker.errors

from autojudge.compiler import compile_submission
from autojudge.config import settings
from autojudge.container_io import _get_file_bytes_safe, _get_file_size_safe, _get_file_text_safe, _put_bytes
from autojudge.languages import SANDBOX_DIR, STDERR_PATH, STDOUT_PATH, LanguageConfig
from autojudge.metrics import RUN_CPU_TIME_SECONDS, RUN_MEMORY_KB, RUN_TIMEOUT_TOTAL, RUN_WALL_TIME_SECONDS
from autojudge.sandbox import (
    ISOLATE_META_PATH,
    _parse_isolate_meta,
    _read_isolate_cgroup_peak_pids,
    _resolve_peak_pids,
    _runtime_isolate_dirs,
    _sync_isolate_cleanup,
    _sync_isolate_init,
    _sync_reset_run_artifacts,
    _sync_run_isolate,
)
from autojudge.types import CompileResult, IsolateError, ProblemLimits, RunResult, SubmissionSource
from autojudge.verdict import compare_output
from shared.enumerations import Verdict

# Re-exports: callers that imported these from autojudge.runner before the
# split can continue to do so without changes.
__all__ = [
    "run_test_case",
    "compile_submission",
    "CompileResult",
    "IsolateError",
    "ProblemLimits",
    "RunResult",
    "SubmissionSource",
    "_parse_isolate_meta",
    "_resolve_peak_pids",
    "_runtime_isolate_dirs",
]

logger = logging.getLogger(__name__)


async def run_test_case(
    container_id: str,
    language: LanguageConfig,
    limits: ProblemLimits,
    artifact_data: bytes,
    input_data: bytes,
    expected_output: bytes,
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
) -> RunResult:
    """
    Run the submission artifact against one test case inside a pool container.

    Steps:
    1. Inject artifact and input into /sandbox/.
    2. Reset the isolate box and stale output files.
    3. Execute the language command via isolate --run.
    4. Enforce an outer safety timeout around the isolate invocation.
    5. Parse the isolate meta file and classify verdicts.
    6. Read stdout/stderr excerpts and compare output when execution succeeds.

    The container is NOT removed here — the worker's finally block handles
    destruction via PoolManager.release() after all test cases finish.

    Args:
        container_id: Docker container ID obtained from PoolManager.acquire().
        language: Language configuration.
        limits: Time and memory limits, already adjusted for language multiplier.
        artifact_data: Raw bytes of the compiled artifact.
        input_data: Raw bytes of the test case input file.
        expected_output: Raw bytes of the expected output file.
        docker_client: Synchronous Docker client.
        executor: ThreadPoolExecutor for blocking Docker SDK calls.

    Returns:
        RunResult with the verdict and resource usage metrics.
    """
    loop = asyncio.get_running_loop()
    cpu_limit_s = limits.time_limit_ms / 1000.0
    inner_wall_limit_s = cpu_limit_s * settings.ISOLATE_WALL_TIME_MULTIPLIER
    outer_timeout_s = inner_wall_limit_s * settings.OUTER_TIMEOUT_MULTIPLIER
    effective_output_limit = settings.OUTPUT_LIMIT_BYTES
    if limits.output_limit_in_bytes is not None:
        effective_output_limit = min(effective_output_limit, limits.output_limit_in_bytes)

    try:
        container = await loop.run_in_executor(
            executor,
            docker_client.containers.get,
            container_id,
        )
    except docker.errors.NotFound:
        logger.error(f"Pool container '{container_id[:12]}' not found — was it killed externally?")
        return RunResult(verdict=Verdict.RE, exit_code=-1)
    except Exception as exc:
        logger.error(f"Failed to look up pool container '{container_id[:12]}': {str(exc)}")
        return RunResult(verdict=Verdict.RE, exit_code=-1)

    try:
        await loop.run_in_executor(executor, _put_bytes, container, artifact_data, language.artifact_path)
        await loop.run_in_executor(executor, _put_bytes, container, input_data, f"{SANDBOX_DIR}/input")
    except docker.errors.NotFound:
        logger.error(
            "put_archive returned 404 — /sandbox missing inside container "
            f"'{container_id[:12]}'. This indicates a pool warmup failure."
        )
        return RunResult(verdict=Verdict.RE, exit_code=-1)
    except Exception as exc:
        logger.error(f"File injection failed on container '{container_id[:12]}': {str(exc)}")
        return RunResult(verdict=Verdict.RE, exit_code=-1)

    try:
        await loop.run_in_executor(executor, _sync_reset_run_artifacts, container)
        await loop.run_in_executor(executor, _sync_isolate_init, container)
    except IsolateError as exc:
        raise exc
    except Exception as exc:
        logger.error(f"Isolate initialization failed on container '{container_id[:12]}': {str(exc)}")
        raise IsolateError(str(exc)) from exc

    exec_future = loop.run_in_executor(
        executor,
        _sync_run_isolate,
        container,
        language,
        limits,
        cpu_limit_s,
        inner_wall_limit_s,
        effective_output_limit,
    )

    try:
        isolate_exit_code = await asyncio.wait_for(exec_future, timeout=outer_timeout_s)
    except TimeoutError:
        with suppress(Exception):
            await loop.run_in_executor(executor, lambda: container.kill(signal="SIGKILL"))
        RUN_TIMEOUT_TOTAL.labels(language_id=language.id, timeout_kind="outer_asyncio").inc()
        return RunResult(verdict=Verdict.TLE, wall_time_ms=int(inner_wall_limit_s * 1000))

    cgroup_peak_pids = await loop.run_in_executor(executor, _read_isolate_cgroup_peak_pids, container)
    with suppress(Exception):
        await loop.run_in_executor(executor, _sync_isolate_cleanup, container)

    meta_text = await loop.run_in_executor(executor, _get_file_text_safe, container, ISOLATE_META_PATH, 32 * 1024)
    if meta_text is None:
        raise IsolateError("Missing isolate meta file after execution")
    meta = _parse_isolate_meta(meta_text, isolate_exit_code=isolate_exit_code)
    peak_pids = _resolve_peak_pids(meta.peak_pids, cgroup_peak_pids)

    if meta.wall_time_ms is not None:
        RUN_WALL_TIME_SECONDS.labels(language_id=language.id).observe(meta.wall_time_ms / 1000)
    if meta.cpu_time_ms is not None:
        RUN_CPU_TIME_SECONDS.labels(language_id=language.id).observe(meta.cpu_time_ms / 1000)
    if meta.memory_kb is not None:
        RUN_MEMORY_KB.labels(language_id=language.id).observe(float(meta.memory_kb))

    stdout_size = await loop.run_in_executor(executor, _get_file_size_safe, container, STDOUT_PATH)
    if stdout_size is not None and stdout_size >= effective_output_limit:
        return RunResult(
            verdict=Verdict.OLE,
            exit_code=meta.exit_code,
            wall_time_ms=meta.wall_time_ms,
            memory_kb=meta.memory_kb,
            output_bytes=stdout_size,
            peak_pids=peak_pids,
            stdout_excerpt=await loop.run_in_executor(
                executor, _get_file_bytes_safe, container, STDOUT_PATH, settings.STDOUT_EXCERPT_BYTES
            ),
        )

    if meta.status == "TO":
        RUN_TIMEOUT_TOTAL.labels(language_id=language.id, timeout_kind="isolate_meta_TO").inc()
        return RunResult(
            verdict=Verdict.TLE,
            exit_code=meta.exit_code,
            wall_time_ms=meta.wall_time_ms,
            memory_kb=meta.memory_kb,
            output_bytes=stdout_size,
            peak_pids=peak_pids,
        )

    if meta.cg_oom_killed:
        return RunResult(
            verdict=Verdict.MLE,
            exit_code=meta.exit_code,
            wall_time_ms=meta.wall_time_ms,
            memory_kb=meta.memory_kb,
            output_bytes=stdout_size,
            peak_pids=peak_pids,
        )

    if (
        meta.status == "SG"
        and meta.exit_signal == 11
        and meta.memory_kb is not None
        and meta.memory_kb >= limits.memory_limit_kb
    ):
        return RunResult(
            verdict=Verdict.MLE,
            exit_code=meta.exit_code,
            wall_time_ms=meta.wall_time_ms,
            memory_kb=meta.memory_kb,
            output_bytes=stdout_size,
            peak_pids=peak_pids,
        )

    if meta.status in {"RE", "SG"}:
        stderr_excerpt = await loop.run_in_executor(
            executor, _get_file_bytes_safe, container, STDERR_PATH, settings.STDERR_EXCERPT_BYTES
        )
        return RunResult(
            verdict=Verdict.RE,
            exit_code=meta.exit_code,
            wall_time_ms=meta.wall_time_ms,
            memory_kb=meta.memory_kb,
            output_bytes=stdout_size,
            peak_pids=peak_pids,
            stderr_excerpt=stderr_excerpt,
        )

    stdout = await loop.run_in_executor(executor, _get_file_bytes_safe, container, STDOUT_PATH, effective_output_limit)
    verdict = compare_output(stdout, expected_output)

    return RunResult(
        verdict=verdict,
        exit_code=meta.exit_code,
        wall_time_ms=meta.wall_time_ms,
        memory_kb=meta.memory_kb,
        output_bytes=len(stdout),
        peak_pids=peak_pids,
        stdout_excerpt=stdout[: settings.STDOUT_EXCERPT_BYTES],
    )
