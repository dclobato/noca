#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import asyncio
from dataclasses import dataclass, field

import pytest

from autojudge.docker_interactive_endpoint import DockerExecEndpoint
from autojudge.interactive_runner import (
    InteractiveAttemptResult,
    finalize_interactive_metadata,
    run_interaction,
)
from autojudge.interactive_transcript import InteractiveTranscript, TranscriptLine
from autojudge.interactive_verdict import FinishedFirst, InteractiveVerdict, WatchdogStalledSide
from autojudge.types import IsolateMeta
from shared.enumerations import CustomValidatorCrashReason, Verdict


@dataclass
class Endpoint:
    stdout: asyncio.Queue[bytes]
    stderr: asyncio.Queue[bytes]
    exit_code: int | None = 0
    signal: int | None = None
    received: bytearray = field(default_factory=bytearray)
    stdin_closed: bool = False
    terminated: bool = False

    async def read_stdout(self, size: int) -> bytes:
        del size
        return await self.stdout.get()

    async def read_stderr(self, size: int) -> bytes:
        del size
        return await self.stderr.get()

    async def write_stdin(self, data: bytes) -> None:
        self.received.extend(data)

    async def close_stdin(self) -> None:
        self.stdin_closed = True

    async def wait(self) -> tuple[int | None, int | None]:
        return self.exit_code, self.signal

    async def terminate(self) -> None:
        self.terminated = True


def endpoint(*chunks: bytes, exit_code: int | None = 0) -> Endpoint:
    stdout: asyncio.Queue[bytes] = asyncio.Queue()
    stderr: asyncio.Queue[bytes] = asyncio.Queue()
    for chunk in (*chunks, b""):
        stdout.put_nowait(chunk)
    stderr.put_nowait(b"")
    return Endpoint(stdout, stderr, exit_code=exit_code)


@pytest.mark.asyncio
async def test_bridge_is_full_duplex_and_propagates_eof() -> None:
    contestant = endpoint(b"question\n")
    validator = endpoint(b"answer\n", exit_code=0)

    result = await run_interaction(contestant, validator, output_limit_bytes=100, watchdog_seconds=1)

    assert validator.received == b"question\n"
    assert contestant.received == b"answer\n"
    assert contestant.stdin_closed and validator.stdin_closed
    assert result.classification.verdict == Verdict.AC


@pytest.mark.asyncio
async def test_test_case_input_parametrizes_the_validator_before_the_conversation() -> None:
    contestant = endpoint(b"question\n")
    validator = endpoint(b"answer\n", exit_code=0)

    result = await run_interaction(
        contestant,
        validator,
        testcase_input=b"7 42\n",
        output_limit_bytes=100,
        watchdog_seconds=1,
    )

    # The case's input reaches the validator ahead of the contestant's first line.
    assert validator.received == b"7 42\nquestion\n"
    assert contestant.received == b"answer\n"
    assert result.classification.verdict == Verdict.AC


@pytest.mark.asyncio
async def test_test_case_input_is_not_recorded_in_the_transcript() -> None:
    contestant = endpoint(b"question\n")
    validator = endpoint(b"answer\n", exit_code=0)

    result = await run_interaction(
        contestant,
        validator,
        testcase_input=b"7 42\n",
        output_limit_bytes=100,
        watchdog_seconds=1,
    )

    # The input is the problem's own data, not part of the conversation.
    assert _entries(result) == [("user", "question"), ("validator", "answer")]


@pytest.mark.asyncio
async def test_test_case_input_does_not_count_against_the_contestant_output_limit() -> None:
    contestant = endpoint(b"hi\n")
    validator = endpoint(b"ok\n", exit_code=0)

    result = await run_interaction(
        contestant,
        validator,
        testcase_input=b"0123456789" * 10,
        output_limit_bytes=20,
        watchdog_seconds=1,
    )

    assert result.classification.verdict == Verdict.AC
    assert result.contestant_output_bytes == len(b"hi\n")


@pytest.mark.asyncio
async def test_output_limit_terminates_both_without_validator_crash() -> None:
    contestant = endpoint(b"12345")
    validator = endpoint()

    result = await run_interaction(contestant, validator, output_limit_bytes=5, watchdog_seconds=1)

    assert result.classification.verdict == Verdict.OLE
    assert contestant.terminated and validator.terminated
    assert result.crash_reason is None


@pytest.mark.asyncio
async def test_watchdog_without_a_complete_message_is_retryable_internal_failure() -> None:
    contestant = endpoint()
    validator = endpoint()
    contestant.stdout = asyncio.Queue()

    result = await run_interaction(
        contestant,
        validator,
        output_limit_bytes=100,
        watchdog_seconds=0.01,
    )

    assert result.crash_reason == CustomValidatorCrashReason.WATCHDOG
    assert result.classification.retryable_validator_failure is True
    assert result.watchdog_stalled_side is None


@pytest.mark.asyncio
async def test_watchdog_after_validator_prompt_is_contestant_tle() -> None:
    contestant = endpoint()
    contestant.stdout = asyncio.Queue()
    validator = endpoint(b"53002399\n")

    result = await run_interaction(
        contestant,
        validator,
        output_limit_bytes=100,
        watchdog_seconds=0.01,
    )

    assert result.crash_reason is None
    assert result.watchdog_stalled_side == "contestant"
    assert result.classification == InteractiveVerdict(Verdict.TLE, False)
    assert result.contestant_output_bytes == 0
    assert _entries(result) == [("validator", "53002399")]


@pytest.mark.asyncio
async def test_watchdog_after_contestant_message_is_retryable_validator_failure() -> None:
    contestant = endpoint(b"!42\n")
    validator = endpoint()
    validator.stdout = asyncio.Queue()

    result = await run_interaction(
        contestant,
        validator,
        output_limit_bytes=100,
        watchdog_seconds=0.01,
    )

    assert result.crash_reason == CustomValidatorCrashReason.WATCHDOG
    assert result.watchdog_stalled_side == "validator"
    assert result.classification == InteractiveVerdict(None, True)


@pytest.mark.asyncio
async def test_partial_validator_prompt_does_not_blame_contestant() -> None:
    contestant = endpoint()
    contestant.stdout = asyncio.Queue()
    validator = endpoint(b"incomplete prompt")

    result = await run_interaction(contestant, validator, output_limit_bytes=100, watchdog_seconds=0.01)

    assert result.watchdog_stalled_side is None
    assert result.classification == InteractiveVerdict(None, True)


def _entries(result: InteractiveAttemptResult) -> list[tuple[str, str]]:
    assert result.transcript is not None
    return [(line.direction, line.line) for line in result.transcript.lines]


@dataclass
class Responder(Endpoint):
    """Endpoint that speaks only after the peer's line has been relayed to it.

    The pre-queued `Endpoint` fake lets one side dump every chunk before the
    other is scheduled, which cannot happen in a real request/response protocol.
    This fake emits its next scripted line only when it receives one, which is
    what the interleaving guarantee actually rests on.
    """

    script: list[bytes] = field(default_factory=list)

    async def write_stdin(self, data: bytes) -> None:
        self.received.extend(data)
        self.stdout.put_nowait(self.script.pop(0) if self.script else b"")

    async def close_stdin(self) -> None:
        self.stdin_closed = True
        self.stdout.put_nowait(b"")


def responder(*script: bytes, opening: bytes = b"", exit_code: int | None = 0) -> Responder:
    stdout: asyncio.Queue[bytes] = asyncio.Queue()
    stderr: asyncio.Queue[bytes] = asyncio.Queue()
    if opening:
        stdout.put_nowait(opening)
    stderr.put_nowait(b"")
    return Responder(stdout, stderr, exit_code=exit_code, script=list(script))


@pytest.mark.asyncio
async def test_transcript_records_both_sides_in_protocol_order() -> None:
    """The bridge relays every byte, so the transcript is the conversation."""
    contestant = responder(b"25000001\n", b"", opening=b"50000001\n")
    validator = responder(b"<\n", b"<\n")

    result = await run_interaction(contestant, validator, output_limit_bytes=1000, watchdog_seconds=1)

    assert _entries(result) == [
        ("user", "50000001"),
        ("validator", "<"),
        ("user", "25000001"),
        ("validator", "<"),
    ]
    assert result.transcript is not None and result.transcript.truncated is False


@pytest.mark.asyncio
async def test_transcript_splits_multiline_chunks_and_rejoins_split_lines() -> None:
    """Chunk boundaries are not line boundaries; entries must still be lines."""
    contestant = endpoint(b"one\ntw", b"o\nthree\r\n", b"tail-no-newline")
    validator = endpoint()

    result = await run_interaction(contestant, validator, output_limit_bytes=1000, watchdog_seconds=1)

    assert _entries(result) == [
        ("user", "one"),
        ("user", "two"),
        ("user", "three"),
        ("user", "tail-no-newline"),
    ]
    assert result.transcript is not None
    assert [line.partial for line in result.transcript.lines] == [False, False, False, True]


@pytest.mark.asyncio
async def test_transcript_truncation_does_not_stop_the_relay_or_the_verdict() -> None:
    """Recording is capture-only: past its cap the bridge keeps working."""
    contestant = endpoint(b"aaaa\n", b"bbbb\n", b"cccc\n")
    validator = endpoint(exit_code=1)

    result = await run_interaction(
        contestant,
        validator,
        output_limit_bytes=1000,
        watchdog_seconds=1,
        transcript_max_bytes=6,
    )

    # Only the first line fit under the cap, but every byte still reached the peer
    # and the validator's clean exit still decided the verdict.
    assert _entries(result) == [("user", "aaaa")]
    assert result.transcript is not None and result.transcript.truncated is True
    assert validator.received == b"aaaa\nbbbb\ncccc\n"
    assert result.classification.verdict == Verdict.WA
    assert result.contestant_output_bytes == 15


def _meta(
    *,
    exit_code: int | None = 0,
    exit_signal: int | None = None,
    oom: bool = False,
) -> IsolateMeta:
    return IsolateMeta(
        status="SG" if exit_signal is not None else None,
        exit_code=exit_code,
        exit_signal=exit_signal,
        wall_time_ms=12,
        cpu_time_ms=3,
        memory_kb=128,
        cg_oom_killed=oom,
    )


def _bridge(
    *,
    verdict: Verdict | None = Verdict.AC,
    crash: CustomValidatorCrashReason | None = None,
    finished_first: FinishedFirst | None = None,
    watchdog_stalled_side: WatchdogStalledSide | None = None,
) -> InteractiveAttemptResult:
    return InteractiveAttemptResult(
        classification=InteractiveVerdict(verdict, verdict is None),
        contestant_exit_code=0,
        contestant_signal=None,
        validator_exit_code=0,
        validator_signal=None,
        transcript=InteractiveTranscript(
            lines=[TranscriptLine(direction="user", line="contestant")],
            truncated=False,
        ),
        contestant_stderr_excerpt=b"",
        validator_stderr_excerpt=b"",
        contestant_output_bytes=10,
        crash_reason=crash,
        finished_first=finished_first,
        watchdog_stalled_side=watchdog_stalled_side,
    )


def test_metadata_preserves_clean_255_as_contestant_re() -> None:
    result = finalize_interactive_metadata(_bridge(), _meta(), _meta(exit_code=255))
    assert result.classification == InteractiveVerdict(Verdict.RE, False)
    assert result.validator_signal is None


def test_metadata_validator_signal_is_retryable_internal_failure() -> None:
    result = finalize_interactive_metadata(
        _bridge(),
        _meta(),
        _meta(exit_code=None, exit_signal=11),
    )
    assert result.classification == InteractiveVerdict(None, True)
    assert result.crash_reason == CustomValidatorCrashReason.SIGNAL


def test_metadata_contestant_oom_precedes_missing_validator_exit() -> None:
    result = finalize_interactive_metadata(
        _bridge(verdict=None, crash=CustomValidatorCrashReason.WATCHDOG),
        _meta(exit_code=None, exit_signal=9, oom=True),
        None,
    )
    assert result.classification == InteractiveVerdict(Verdict.MLE, False)
    assert result.crash_reason is None


def test_metadata_preserves_contestant_watchdog_tle_without_crash_reason() -> None:
    result = finalize_interactive_metadata(
        _bridge(
            verdict=Verdict.TLE,
            watchdog_stalled_side="contestant",
        ),
        None,
        None,
    )

    assert result.classification == InteractiveVerdict(Verdict.TLE, False)
    assert result.crash_reason is None
    assert result.watchdog_stalled_side == "contestant"


def test_metadata_preserves_validator_first_precedence() -> None:
    result = finalize_interactive_metadata(
        _bridge(verdict=Verdict.TLE, finished_first="validator"),
        _meta(exit_code=1),
        _meta(exit_code=2),
    )
    assert result.classification == InteractiveVerdict(Verdict.TLE, False)


def test_metadata_clean_contestant_first_keeps_the_validator_verdict() -> None:
    result = finalize_interactive_metadata(
        _bridge(finished_first="contestant"),
        _meta(exit_code=0),
        _meta(exit_code=2),
    )
    assert result.classification == InteractiveVerdict(Verdict.TLE, False)


def test_metadata_contestant_crashing_first_is_runtime_error() -> None:
    result = finalize_interactive_metadata(
        _bridge(finished_first="contestant"),
        _meta(exit_code=None, exit_signal=11),
        _meta(exit_code=1),
    )
    assert result.classification == InteractiveVerdict(Verdict.RE, False)


@pytest.mark.asyncio
async def test_docker_frame_suffix_is_retained_across_bounded_reads() -> None:
    """A protocol frame larger than one pump read must not lose bytes."""
    queue: asyncio.Queue[bytes] = asyncio.Queue()
    await queue.put(b"abcdefgh")

    first, remainder = await DockerExecEndpoint._read_bounded(queue, b"", 3)
    second, remainder = await DockerExecEndpoint._read_bounded(queue, remainder, 3)
    third, remainder = await DockerExecEndpoint._read_bounded(queue, remainder, 3)

    assert first + second + third == b"abcdefgh"
    assert remainder == b""
