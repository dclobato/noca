#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Shared dataclasses and exception types for the autojudge module.

This module has no internal autojudge dependencies — only shared.enumerations
and stdlib. All other autojudge modules import their types from here instead of
defining their own, avoiding cross-module coupling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TypedDict

from shared.enumerations import JudgmentStatus, ProfilingStatus, Verdict

# ---------------------------------------------------------------------------
# Runner types (compile and run phases)
# ---------------------------------------------------------------------------


@dataclass
class CompileResult:
    """
    Outcome of the compile phase.

    On success, artifact_data holds the raw bytes of the compiled artifact
    (binary, jar, or source file) ready to be injected into a run container.
    On failure, artifact_data is None and compile_log contains the compiler's
    stderr.
    """

    success: bool
    exit_code: int
    compile_log: str
    artifact_data: bytes | None = None
    wall_time_ms: int | None = None


@dataclass
class RunResult:
    """
    Outcome of running the submission against one test case.

    Produced by run_test_case() and consumed by the worker to persist a
    submission_test_result row and aggregate the final verdict.
    """

    verdict: Verdict

    wall_time_ms: int | None = None
    memory_kb: int | None = None
    output_bytes: int | None = None
    peak_pids: int | None = None
    exit_code: int | None = None

    stdout_excerpt: bytes = field(default_factory=bytes)
    stderr_excerpt: bytes = field(default_factory=bytes)
    checker_output: str | None = None


@dataclass(frozen=True)
class SubmissionSource:
    """Submission payload passed to the compile phase."""

    judgment_id: str
    submission_id: str
    source_code: str


@dataclass(frozen=True)
class ProblemLimits:
    """
    Resource limits for a single problem, adjusted for the language.
    Passed to run_test_case() so it does not need to know about Problem models.
    """

    time_limit_ms: int
    memory_limit_kb: int
    pids_limit: int
    output_limit_in_bytes: int | None = None
    repetitions: int = 1


@dataclass(frozen=True)
class IsolateMeta:
    """Parsed contents of the isolate meta file."""

    status: str | None
    exit_code: int | None
    exit_signal: int | None
    wall_time_ms: int | None
    cpu_time_ms: int | None
    memory_kb: int | None
    peak_pids: int | None = None
    cg_oom_killed: bool = False


class IsolateError(RuntimeError):
    """Raised when isolate itself fails or its meta file is unusable."""


# ---------------------------------------------------------------------------
# Worker types (job processing)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepetitionCaseResult:
    """Aggregated result for one test case across all configured repetitions."""

    verdict: Verdict
    total_wall_time_ms: int | None
    peak_memory_kb: int | None
    peak_output_bytes: int | None
    peak_pids: int | None
    exit_code: int | None
    stdout_excerpt: bytes
    stderr_excerpt: bytes


# ---------------------------------------------------------------------------
# Database access types
# ---------------------------------------------------------------------------


class _JudgmentState(TypedDict):
    """Internal state snapshot used by judgment transition methods."""

    submission_id: str
    status: JudgmentStatus | None
    autojudge_verdict: Verdict | None


@dataclass(frozen=True)
class QueuedSubmission:
    """Submission payload loaded from the database for judging."""

    judgment_id: str
    submission_id: str
    contest_id: str
    contest_start_time: datetime
    problem_id: str
    team_id: str
    language_id: str
    source_code: str
    autojudge_only: bool
    accept_pe: bool
    stop_updating_scoreboard: int


@dataclass(frozen=True)
class QueuedProfilingRun:
    """Profiling run payload loaded from the database."""

    profiling_run_id: str
    contest_id: str
    problem_id: str
    language_id: str
    source_code: str
    safety_factor: float


@dataclass(frozen=True)
class ArenaQueuedTestCase:
    """Arena test case payload loaded from database text columns."""

    test_case_id: str
    ordinal: int
    input_data: bytes
    expected_output: bytes


@dataclass(frozen=True)
class QueuedArenaSubmission:
    """Arena submission payload loaded from the database for judging."""

    judgment_id: str
    submission_id: str
    user_id: str
    problem_id: str
    problem_number: int
    problem_title: str
    language_id: str
    source_code: str
    limits: ProblemLimits
    test_cases: tuple[ArenaQueuedTestCase, ...]


@dataclass(frozen=True)
class RecoverableSubmissionJob:
    """A non-terminal submission judgment that can be re-enqueued at startup."""

    status: JudgmentStatus
    payload: QueuedSubmission


@dataclass(frozen=True)
class RecoverableProfilingJob:
    """A non-terminal profiling run that can be re-enqueued at startup."""

    status: ProfilingStatus
    payload: QueuedProfilingRun


@dataclass(frozen=True)
class RecoverableArenaSubmissionJob:
    """A non-terminal Arena submission judgment that can be re-enqueued."""

    status: JudgmentStatus
    payload: QueuedArenaSubmission


@dataclass(frozen=True)
class ProfilingObservedLimits:
    """Observed resource peaks after applying the safety factor."""

    time_limit_ms: int
    memory_limit_kb: int
    pids_limit: int
    output_limit_in_bytes: int
