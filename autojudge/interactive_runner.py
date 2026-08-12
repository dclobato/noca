#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Full-duplex protocol bridge used by Docker interactive executions.

An interactive judgment runs one container pair for the whole submission and
replays it once per test case: :func:`prepare_interactive_containers` copies the
compiled artifacts in once, then :func:`run_docker_interaction` runs a single
test case, parametrizing the validator by writing that case's input to its stdin
before the two processes start talking.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

import docker

from autojudge.container_io import _get_file_text_safe, _put_bytes
from autojudge.docker_interactive_endpoint import DockerExecEndpoint
from autojudge.interactive_transcript import (
    TRANSCRIPT_MAX_BYTES,
    InteractiveTranscript,
    TranscriptDirection,
    TranscriptRecorder,
)
from autojudge.interactive_verdict import (
    FinishedFirst,
    InteractiveOutcome,
    InteractiveVerdict,
    WatchdogStalledSide,
    classify_interactive_outcome,
)
from autojudge.languages import LanguageConfig
from autojudge.sandbox import (
    ISOLATE_META_PATH,
    _parse_isolate_meta,
    _sync_isolate_init,
    _sync_reset_run_artifacts,
    build_interactive_isolate_command,
    build_validator_isolate_command,
)
from autojudge.types import IsolateMeta, ProblemLimits
from shared.enumerations import CustomValidatorCrashReason, Verdict


class InteractiveEndpoint(Protocol):
    """Asynchronous process stream controlled by the interactive bridge."""

    async def read_stdout(self, size: int) -> bytes: ...

    async def write_stdin(self, data: bytes) -> None: ...

    async def close_stdin(self) -> None: ...

    async def read_stderr(self, size: int) -> bytes: ...

    async def wait(self) -> tuple[int | None, int | None]: ...

    async def terminate(self) -> None: ...


@dataclass(frozen=True)
class InteractiveAttemptResult:
    """Verdict and bounded diagnostics from one complete interaction."""

    classification: InteractiveVerdict
    contestant_exit_code: int | None
    contestant_signal: int | None
    validator_exit_code: int | None
    validator_signal: int | None
    transcript: InteractiveTranscript | None
    contestant_stderr_excerpt: bytes
    validator_stderr_excerpt: bytes
    contestant_output_bytes: int
    crash_reason: CustomValidatorCrashReason | None
    wall_time_ms: int | None = None
    memory_kb: int | None = None
    finished_first: FinishedFirst | None = None
    watchdog_stalled_side: WatchdogStalledSide | None = None


class _OutputLimitReached(Exception):
    """Internal control flow when contestant stdout reaches its limit."""


async def prepare_interactive_containers(
    *,
    contestant_container_id: str,
    validator_container_id: str,
    contestant_language: LanguageConfig,
    validator_language: LanguageConfig,
    contestant_artifact: bytes,
    validator_artifact: bytes,
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
) -> None:
    """Copy both compiled artifacts into a freshly acquired container pair.

    Run once per container pair. Resetting a run only clears stdout, stderr and
    the isolate meta file, so the artifacts stay in place for every later case.
    """
    loop = asyncio.get_running_loop()
    contestant_container, validator_container = await asyncio.gather(
        loop.run_in_executor(executor, docker_client.containers.get, contestant_container_id),
        loop.run_in_executor(executor, docker_client.containers.get, validator_container_id),
    )
    await asyncio.gather(
        loop.run_in_executor(
            executor,
            _put_bytes,
            contestant_container,
            contestant_artifact,
            contestant_language.artifact_path,
        ),
        loop.run_in_executor(
            executor,
            _put_bytes,
            validator_container,
            validator_artifact,
            validator_language.artifact_path,
        ),
    )


async def run_docker_interaction(
    *,
    contestant_container_id: str,
    validator_container_id: str,
    contestant_language: LanguageConfig,
    validator_language: LanguageConfig,
    testcase_input: bytes,
    limits: ProblemLimits,
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
    output_limit_bytes: int,
    watchdog_seconds: float,
    validator_environment: dict[str, str] | None = None,
) -> InteractiveAttemptResult:
    """Run one test case on an already prepared container pair.

    The containers must have been through :func:`prepare_interactive_containers`
    first. ``testcase_input`` parametrizes the validator: it is written to the
    validator's stdin before the conversation starts.
    """
    loop = asyncio.get_running_loop()
    contestant_container, validator_container = await asyncio.gather(
        loop.run_in_executor(executor, docker_client.containers.get, contestant_container_id),
        loop.run_in_executor(executor, docker_client.containers.get, validator_container_id),
    )
    await asyncio.gather(
        loop.run_in_executor(executor, _sync_reset_run_artifacts, contestant_container),
        loop.run_in_executor(executor, _sync_reset_run_artifacts, validator_container),
    )
    await asyncio.gather(
        loop.run_in_executor(executor, _sync_isolate_init, contestant_container),
        loop.run_in_executor(executor, _sync_isolate_init, validator_container),
    )
    contestant_endpoint, validator_endpoint = await asyncio.gather(
        DockerExecEndpoint.start(
            docker_client=docker_client,
            container_id=contestant_container_id,
            command=build_interactive_isolate_command(contestant_container, contestant_language, limits),
            executor=executor,
        ),
        DockerExecEndpoint.start(
            docker_client=docker_client,
            container_id=validator_container_id,
            command=build_validator_isolate_command(
                validator_container,
                validator_language,
                validator_environment,
            ),
            executor=executor,
        ),
    )
    bridge_result = await run_interaction(
        contestant_endpoint,
        validator_endpoint,
        testcase_input=testcase_input,
        output_limit_bytes=output_limit_bytes,
        watchdog_seconds=watchdog_seconds,
    )
    contestant_meta_text, validator_meta_text = await asyncio.gather(
        loop.run_in_executor(executor, _get_file_text_safe, contestant_container, ISOLATE_META_PATH, 32 * 1024),
        loop.run_in_executor(executor, _get_file_text_safe, validator_container, ISOLATE_META_PATH, 32 * 1024),
    )
    contestant_meta = None
    validator_meta = None
    if contestant_meta_text is not None:
        with suppress(Exception):
            contestant_meta = _parse_isolate_meta(
                contestant_meta_text,
                isolate_exit_code=bridge_result.contestant_exit_code or 0,
            )
    if validator_meta_text is not None:
        with suppress(Exception):
            validator_meta = _parse_isolate_meta(
                validator_meta_text,
                isolate_exit_code=bridge_result.validator_exit_code or 0,
            )

    return finalize_interactive_metadata(bridge_result, contestant_meta, validator_meta)


def finalize_interactive_metadata(
    bridge_result: InteractiveAttemptResult,
    contestant_meta: IsolateMeta | None,
    validator_meta: IsolateMeta | None,
) -> InteractiveAttemptResult:
    """Replace enclosing-isolate exits with actual child metadata and classify."""
    memory_limit_reached = bool(contestant_meta and contestant_meta.cg_oom_killed)
    output_limit_reached = bridge_result.classification.verdict == Verdict.OLE
    judge_terminated = memory_limit_reached or output_limit_reached
    contestant_watchdog = bridge_result.watchdog_stalled_side == "contestant"
    crash_reason = None if judge_terminated or contestant_watchdog else bridge_result.crash_reason
    if not judge_terminated and not contestant_watchdog and crash_reason is None and validator_meta is None:
        crash_reason = CustomValidatorCrashReason.COMMUNICATION
    elif (
        not judge_terminated
        and not contestant_watchdog
        and crash_reason is None
        and validator_meta is not None
        and validator_meta.exit_signal is not None
    ):
        crash_reason = CustomValidatorCrashReason.SIGNAL

    contestant_exit_code = contestant_meta.exit_code if contestant_meta is not None else None
    contestant_signal = contestant_meta.exit_signal if contestant_meta is not None else None
    validator_exit_code = validator_meta.exit_code if validator_meta is not None else None
    validator_signal = validator_meta.exit_signal if validator_meta is not None else None
    classification = classify_interactive_outcome(
        InteractiveOutcome(
            contestant_exit_code,
            contestant_signal,
            validator_exit_code,
            validator_signal,
            memory_limit_reached=memory_limit_reached,
            output_limit_reached=output_limit_reached,
            crash_reason=crash_reason,
            finished_first=bridge_result.finished_first,
            watchdog_stalled_side=bridge_result.watchdog_stalled_side,
        )
    )
    return InteractiveAttemptResult(
        classification=classification,
        contestant_exit_code=contestant_exit_code,
        contestant_signal=contestant_signal,
        validator_exit_code=validator_exit_code,
        validator_signal=validator_signal,
        transcript=bridge_result.transcript,
        contestant_stderr_excerpt=bridge_result.contestant_stderr_excerpt,
        validator_stderr_excerpt=bridge_result.validator_stderr_excerpt,
        contestant_output_bytes=bridge_result.contestant_output_bytes,
        crash_reason=crash_reason,
        wall_time_ms=contestant_meta.wall_time_ms if contestant_meta else None,
        memory_kb=contestant_meta.memory_kb if contestant_meta else None,
        finished_first=bridge_result.finished_first,
        watchdog_stalled_side=bridge_result.watchdog_stalled_side,
    )


async def run_interaction(
    contestant: InteractiveEndpoint,
    validator: InteractiveEndpoint,
    *,
    testcase_input: bytes = b"",
    output_limit_bytes: int,
    watchdog_seconds: float,
    excerpt_bytes: int = 16_384,
    transcript_max_bytes: int = TRANSCRIPT_MAX_BYTES,
) -> InteractiveAttemptResult:
    """Bridge both processes, preserve EOF, enforce output limit, and classify.

    ``testcase_input`` is written to the validator's stdin before the contestant
    is relayed, so the validator can read the case it must play. Those bytes are
    the problem's own data, not part of the conversation, so they are deliberately
    kept out of the transcript.

    Every byte the two sides exchange is relayed through this bridge, so the
    recorder observes the conversation in protocol order. The two pumps are
    separate tasks, so that order is "as observed by the judge" rather than a
    causal proof — but these protocols are strict request/response (neither side
    can speak until the peer's line has been relayed to it), so observed order is
    protocol order.
    """
    recorder = TranscriptRecorder(max_bytes=transcript_max_bytes)
    contestant_stderr = bytearray()
    validator_stderr = bytearray()
    contestant_output_bytes = 0
    output_limited = False
    crash_reason: CustomValidatorCrashReason | None = None
    finished_first: FinishedFirst | None = None
    watchdog_stalled_side: WatchdogStalledSide | None = None
    last_complete_message: TranscriptDirection | None = None
    protocol_line_buffers: dict[TranscriptDirection, bytearray] = {
        "user": bytearray(),
        "validator": bytearray(),
    }

    def observe_completed_protocol_lines(direction: TranscriptDirection, chunk: bytes) -> None:
        """Track the latest complete line independently of transcript truncation."""
        nonlocal last_complete_message
        buffer = protocol_line_buffers[direction]
        buffer.extend(chunk)
        if (last_newline := buffer.rfind(b"\n")) == -1:
            return
        del buffer[: last_newline + 1]
        last_complete_message = direction

    async def pump(
        source: InteractiveEndpoint,
        destination: InteractiveEndpoint,
        direction: TranscriptDirection,
        *,
        count_contestant_output: bool,
        preamble: bytes = b"",
    ) -> None:
        nonlocal contestant_output_bytes
        if preamble:
            await destination.write_stdin(preamble)
        while chunk := await source.read_stdout(65_536):
            # Recording is capture-only: past its cap it silently stops growing
            # while the relay below keeps running, so a verdict never depends on it.
            recorder.record(direction, chunk)
            if count_contestant_output:
                contestant_output_bytes += len(chunk)
                if contestant_output_bytes >= output_limit_bytes:
                    raise _OutputLimitReached
            await destination.write_stdin(chunk)
            observe_completed_protocol_lines(direction, chunk)
        recorder.close(direction)
        await destination.close_stdin()

    async def capture_stderr(endpoint: InteractiveEndpoint, excerpt: bytearray) -> None:
        while chunk := await endpoint.read_stderr(65_536):
            if len(excerpt) < excerpt_bytes:
                excerpt.extend(chunk[: excerpt_bytes - len(excerpt)])

    async def watch_first_exit() -> None:
        nonlocal finished_first
        contestant_wait = asyncio.create_task(contestant.wait())
        validator_wait = asyncio.create_task(validator.wait())
        try:
            done, pending = await asyncio.wait(
                {contestant_wait, validator_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if len(done) == 1 and pending:
                finished_first = "contestant" if contestant_wait in done else "validator"
            await asyncio.gather(contestant_wait, validator_wait, return_exceptions=True)
        except asyncio.CancelledError:
            contestant_wait.cancel()
            validator_wait.cancel()
            await asyncio.gather(contestant_wait, validator_wait, return_exceptions=True)
            raise

    first_exit_task = asyncio.create_task(watch_first_exit())
    try:
        async with asyncio.timeout(watchdog_seconds):
            try:
                await asyncio.gather(
                    pump(
                        contestant,
                        validator,
                        "user",
                        count_contestant_output=True,
                        preamble=testcase_input,
                    ),
                    pump(validator, contestant, "validator", count_contestant_output=False),
                    capture_stderr(contestant, contestant_stderr),
                    capture_stderr(validator, validator_stderr),
                )
            except _OutputLimitReached:
                output_limited = True
                await asyncio.gather(contestant.terminate(), validator.terminate(), return_exceptions=True)
    except TimeoutError:
        crash_reason = CustomValidatorCrashReason.WATCHDOG
        if finished_first is None:
            if last_complete_message == "validator":
                watchdog_stalled_side = "contestant"
            elif last_complete_message == "user":
                watchdog_stalled_side = "validator"
        await asyncio.gather(contestant.terminate(), validator.terminate(), return_exceptions=True)
    except Exception:
        crash_reason = CustomValidatorCrashReason.COMMUNICATION
        await asyncio.gather(contestant.terminate(), validator.terminate(), return_exceptions=True)

    await first_exit_task
    contestant_exit, contestant_signal = await contestant.wait()
    validator_exit, validator_signal = await validator.wait()
    reported_crash_reason = None if watchdog_stalled_side == "contestant" else crash_reason
    outcome = InteractiveOutcome(
        contestant_exit,
        contestant_signal,
        validator_exit,
        validator_signal,
        output_limit_reached=output_limited,
        crash_reason=reported_crash_reason,
        finished_first=finished_first,
        watchdog_stalled_side=watchdog_stalled_side,
    )
    return InteractiveAttemptResult(
        classification=classify_interactive_outcome(outcome),
        contestant_exit_code=contestant_exit,
        contestant_signal=contestant_signal,
        validator_exit_code=validator_exit,
        validator_signal=validator_signal,
        transcript=recorder.build(),
        contestant_stderr_excerpt=bytes(contestant_stderr),
        validator_stderr_excerpt=bytes(validator_stderr),
        contestant_output_bytes=contestant_output_bytes,
        crash_reason=reported_crash_reason,
        finished_first=finished_first,
        watchdog_stalled_side=watchdog_stalled_side,
    )
