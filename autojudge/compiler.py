#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Compile phase of the judge pipeline.

Public API
----------
    compile_submission(submission, language, docker_client, executor) -> CompileResult

Creates a short-lived container from language.compile_image, injects the
source code, runs language.compile_cmd, extracts the artifact, and removes
the container. The entire operation is bounded by language.compile_timeout_s.

For interpreted languages, compile_cmd performs only a syntax check.
The artifact returned is the source file itself.
"""

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

import docker
import docker.errors

from autojudge.container_io import _get_file_bytes, _put_bytes
from autojudge.languages import SANDBOX_DIR, LanguageConfig
from autojudge.metrics import COMPILE_DURATION_SECONDS, COMPILE_TOTAL
from autojudge.types import CompileResult, SubmissionSource

logger = logging.getLogger(__name__)


async def compile_submission(
    submission: SubmissionSource,
    language: LanguageConfig,
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
) -> CompileResult:
    """
    Run the compile phase for a submission.

    Args:
        submission: The DB-loaded submission payload for this judgment attempt.
        language: Language configuration from the registry.
        docker_client: Synchronous Docker client (calls run in executor).
        executor: ThreadPoolExecutor for Docker SDK calls.

    Returns:
        CompileResult with success=True and artifact_data set on success, or
        success=False and compile_log set if the compiler returned non-zero.
    """
    loop = asyncio.get_running_loop()
    source_bytes = submission.source_code.encode()
    container_source_path = f"{SANDBOX_DIR}/{language.source_filename}"

    logger.info(f"Compile phase started for submission '{submission.submission_id}', language '{language.id}'")

    start = time.monotonic()

    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                executor,
                _sync_compile,
                language,
                docker_client,
                source_bytes,
                container_source_path,
                submission.submission_id,
            ),
            timeout=language.compile_timeout_s,
        )
    except TimeoutError:
        wall_ms = int((time.monotonic() - start) * 1000)
        logger.warning(f"Compile phase timed out for submission '{submission.submission_id}' after {wall_ms} ms")
        COMPILE_TOTAL.labels(language_id=language.id, outcome="timeout").inc()
        COMPILE_DURATION_SECONDS.labels(language_id=language.id, outcome="timeout").observe(wall_ms / 1000)
        return CompileResult(
            success=False,
            exit_code=-1,
            compile_log=f"Compilation timed out after {language.compile_timeout_s:.0f}s",
            wall_time_ms=wall_ms,
        )

    result.wall_time_ms = int((time.monotonic() - start) * 1000)
    _outcome = "success" if result.success else "failure"
    COMPILE_TOTAL.labels(language_id=language.id, outcome=_outcome).inc()
    COMPILE_DURATION_SECONDS.labels(language_id=language.id, outcome=_outcome).observe(result.wall_time_ms / 1000)

    logger.info("Compile phase finished")
    logger.info(
        json.dumps(
            {
                "submission_id": submission.submission_id,
                "success": result.success,
                "exit_code": result.exit_code,
                "wall_ms": result.wall_time_ms,
            },
            indent=2,
        )
    )
    return result


def _sync_compile(
    language: LanguageConfig,
    client: docker.DockerClient,
    source_bytes: bytes,
    container_source_path: str,
    submission_id: str,
) -> CompileResult:
    """
    Synchronous compile implementation — runs in ThreadPoolExecutor.

    Creates container → injects source → runs compile_cmd →
    extracts artifact → removes container.

    Args:
        language: Language configuration.
        client: Synchronous Docker client.
        source_bytes: Raw source code bytes.
        container_source_path: Destination path inside the container.
        submission_id: Used for container labels and error messages.
    """
    container = None
    try:
        container = client.containers.run(
            image=language.compile_image,
            command=["sleep", "infinity"],
            detach=True,
            network_mode="none",
            read_only=False,
            mem_limit="512m",
            memswap_limit="512m",
            pids_limit=128,
            labels={
                "noca.role": "judge-compile",
                "noca.language": language.id,
                "noca.submission_id": submission_id,
            },
        )

        container.exec_run(["mkdir", "-p", SANDBOX_DIR], demux=False)
        _put_bytes(container, source_bytes, container_source_path)

        if language.compile_cmd is not None:
            exec_result = container.exec_run(
                language.compile_cmd,
                stdout=True,
                stderr=True,
                demux=False,
            )
            exit_code = exec_result.exit_code
            raw_output = exec_result.output or b""
        else:
            exit_code = 0
            raw_output = b""

        compile_log = raw_output.decode(errors="replace")[:8192]

        if exit_code != 0:
            return CompileResult(
                success=False,
                exit_code=exit_code,
                compile_log=compile_log,
            )

        if language.artifact_is_source:
            artifact_data = source_bytes
        else:
            artifact_data = _get_file_bytes(container, language.artifact_path)

        return CompileResult(
            success=True,
            exit_code=0,
            compile_log=compile_log,
            artifact_data=artifact_data,
        )

    except docker.errors.ImageNotFound:
        return CompileResult(
            success=False,
            exit_code=-1,
            compile_log=(
                f"Compile image '{language.compile_image}' not found. Build the judge images before running the worker."
            ),
        )
    except Exception as exc:
        logger.exception(f"Unexpected error in compile phase for submission '{submission_id}': {str(exc)}")
        return CompileResult(
            success=False,
            exit_code=-1,
            compile_log=f"Internal judge error during compilation: {exc}",
        )
    finally:
        if container is not None:
            with suppress(Exception):
                container.kill(signal="SIGKILL")
            with suppress(Exception):
                container.remove(force=True)
