#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Test case loading and submission job processing pipeline.

Provides:
- _load_test_cases()        — load test case bytes from the shared filesystem
- _run_repeated_test_case() — run one test case across the configured repetition count
- process_submission_job()  — full compile → run → publish pipeline for one submission
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path

import docker
import valkey.asyncio as aiovalkey

from autojudge.compiler import compile_submission
from autojudge.config import settings
from autojudge.custom_validator_submission import prepare_custom_validator, run_custom_validator_submission
from autojudge.db import DatabaseAccess
from autojudge.languages import LanguageConfig
from autojudge.metrics import SUBMISSION_DURATION_SECONDS, TEST_CASES_RUN_TOTAL, VERDICTS_TOTAL
from autojudge.pool import PoolExhaustedError, PoolManager, PoolShutdownError
from autojudge.queue_ops import publish_verdict
from autojudge.runner import run_test_case
from autojudge.runtime_utils import is_recoverable_isolate_runtime_error
from autojudge.types import (
    ProblemLimits,
    QueuedSubmission,
    RepetitionCaseResult,
    SubmissionSource,
    case_budget_ms,
)
from autojudge.verdict import CaseResult, aggregate_verdict, worst_resource_usage
from shared.enumerations import Verdict
from shared.language_registry import get_language
from shared.queue_schema import VerdictEvent
from shared.services.scoreboard_cache import invalidate_contest_result_caches
from shared.tc_zip import normalize_testcase_bytes

logger = logging.getLogger(__name__)

Valkey_Client = aiovalkey.Valkey

# Maximum bytes read from a test case file (input or expected output).
_TEST_FILE_MAX_BYTES = 256 * 1024 * 1024  # 256 MB


def _load_test_case_inputs(problem_id: str, testcase_dir: Path | None = None) -> list[tuple[int, bytes]]:
    """
    Load the inputs of every test case, ignoring expected output.

    Interactive (custom-validator) problems have no expected output: each case's
    input parametrizes the validator instead.

    Args:
        problem_id: UUID of the problem whose test cases to load.
        testcase_dir: Domain-specific root containing ``<problem_id>/NNN.in``.
            Defaults to the Web (contest) root when omitted.

    Returns:
        List of (ordinal, input_bytes) tuples sorted by ordinal.

    Raises:
        FileNotFoundError: If the problem directory does not exist.
        ValueError: If no test cases exist, or a file is not named ``NNN.in``.
    """
    root = testcase_dir if testcase_dir is not None else settings.contest_testcase_dir
    base = Path(root) / problem_id

    if not base.is_dir():
        raise FileNotFoundError(f"Test case directory not found: {base}. Has this problem been uploaded?")

    input_files = sorted(base.glob("*.in"))

    if not input_files:
        raise ValueError(f"No test case files (*.in) found in {base}. Has this problem been uploaded?")

    cases: list[tuple[int, bytes]] = []
    for in_path in input_files:
        try:
            ordinal = int(in_path.stem)
        except ValueError as exc:
            raise ValueError(f"Test case file is not named NNN.in: {in_path.name}") from exc
        cases.append((ordinal, normalize_testcase_bytes(in_path.read_bytes()[:_TEST_FILE_MAX_BYTES])))

    return cases


def _load_test_cases(problem_id: str, testcase_dir: Path | None = None) -> list[tuple[bytes, bytes]]:
    """
    Load all test cases for a problem from the shared filesystem.

    Args:
        problem_id: UUID of the problem whose test cases to load.
        testcase_dir: Domain-specific root containing ``<problem_id>/NNN.in|out``.
            Defaults to the Web (contest) root when omitted.

    Returns:
        List of (input_bytes, expected_output_bytes) tuples sorted by ordinal.

    Raises:
        FileNotFoundError: If the problem directory does not exist.
        ValueError: If a .in file has no .out file, or no test cases exist.
    """
    root = testcase_dir if testcase_dir is not None else settings.contest_testcase_dir
    base = Path(root) / problem_id

    if not base.is_dir():
        raise FileNotFoundError(f"Test case directory not found: {base}. Has this problem been uploaded?")

    input_files = sorted(base.glob("*.in"))

    if not input_files:
        raise ValueError(f"No test case files (*.in) found in {base}. Has this problem been uploaded?")

    cases: list[tuple[bytes, bytes]] = []
    for in_path in input_files:
        out_path = in_path.with_suffix(".out")
        if not out_path.exists():
            raise ValueError(f"Missing expected output file for {in_path.name}: {out_path} does not exist.")
        input_data = normalize_testcase_bytes(in_path.read_bytes()[:_TEST_FILE_MAX_BYTES])
        expected_data = normalize_testcase_bytes(out_path.read_bytes()[:_TEST_FILE_MAX_BYTES])
        cases.append((input_data, expected_data))

    return cases


async def _run_repeated_test_case(
    *,
    container_id: str,
    language: LanguageConfig,
    limits: ProblemLimits,
    artifact_data: bytes,
    input_data: bytes,
    expected_output: bytes,
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
    per_run_ceiling_ms: int | None = None,
) -> RepetitionCaseResult:
    """
    Run one test case across the problem repetition count under a shared time budget.

    ``limits.time_limit_ms`` is the limit for a single repetition, so the budget
    for the case is the product with ``limits.repetitions``. That budget is
    spent, not re-imposed per repetition: each run is launched with whatever is
    left, so a slow repetition borrows from a fast one and the case is TLE only
    once the whole budget is gone. TLE therefore means the *mean* run exceeded
    the stated limit, which is what makes repetitions smooth measurement noise
    instead of giving a submission N chances to get unlucky.

    ``per_run_ceiling_ms`` bounds a single execution on top of that. Judging a
    submission leaves it unset, because borrowing is the whole point of a
    problem's time limit. Profiling sets it, because the number it runs under is
    not a problem's limit at all but an infrastructure cap
    (``NOCA_JUDGE_PROFILING_MAX_CPU_TIME_SEC``) whose job is to bound *one*
    execution -- without it, a hung reference implementation would take the
    entire aggregate budget in its first run and hold a judge container for the
    whole of it.

    Args:
        container_id: Pool container to use.
        language: Language configuration.
        limits: Resource limits including repetition count.
        artifact_data: Compiled artifact bytes.
        input_data: Test case input bytes.
        expected_output: Expected output bytes for comparison.
        docker_client: Synchronous Docker client.
        executor: ThreadPoolExecutor for Docker SDK calls.
        per_run_ceiling_ms: Hard ceiling for any single execution, or None to let
            one repetition draw on the whole remaining budget.

    Returns:
        RepetitionCaseResult with aggregated verdict and resource peaks.
    """

    remaining_budget_ms = case_budget_ms(limits)
    total_wall_time_ms = 0
    peak_memory_kb: int | None = None
    peak_output_bytes: int | None = None
    peak_pids: int | None = None
    stdout_excerpt = b""
    stderr_excerpt = b""
    exit_code: int | None = None
    exit_signal: int | None = None

    for _ in range(max(1, limits.repetitions)):
        if remaining_budget_ms < 1:
            return RepetitionCaseResult(
                verdict=Verdict.TLE,
                total_wall_time_ms=total_wall_time_ms,
                peak_memory_kb=peak_memory_kb,
                peak_output_bytes=peak_output_bytes,
                peak_pids=peak_pids,
                exit_code=exit_code,
                exit_signal=exit_signal,
                stdout_excerpt=stdout_excerpt,
                stderr_excerpt=stderr_excerpt,
            )

        run_limit_ms = remaining_budget_ms
        if per_run_ceiling_ms is not None:
            run_limit_ms = min(run_limit_ms, per_run_ceiling_ms)
        repetition_limits = ProblemLimits(
            time_limit_ms=run_limit_ms,
            memory_limit_kb=limits.memory_limit_kb,
            pids_limit=limits.pids_limit,
            output_limit_in_bytes=limits.output_limit_in_bytes,
            repetitions=1,
        )
        run_result = await run_test_case(
            container_id=container_id,
            language=language,
            limits=repetition_limits,
            artifact_data=artifact_data,
            input_data=input_data,
            expected_output=expected_output,
            docker_client=docker_client,
            executor=executor,
        )
        exit_code = run_result.exit_code
        exit_signal = run_result.exit_signal
        stdout_excerpt = run_result.stdout_excerpt or stdout_excerpt
        stderr_excerpt = run_result.stderr_excerpt or stderr_excerpt

        if run_result.memory_kb is not None:
            peak_memory_kb = (
                run_result.memory_kb if peak_memory_kb is None else max(peak_memory_kb, run_result.memory_kb)
            )
        if run_result.output_bytes is not None:
            peak_output_bytes = (
                run_result.output_bytes
                if peak_output_bytes is None
                else max(peak_output_bytes, run_result.output_bytes)
            )
        if run_result.peak_pids is not None:
            peak_pids = run_result.peak_pids if peak_pids is None else max(peak_pids, run_result.peak_pids)

        if run_result.wall_time_ms is not None:
            total_wall_time_ms += run_result.wall_time_ms
            remaining_budget_ms = max(0, remaining_budget_ms - run_result.wall_time_ms)

        if run_result.verdict != Verdict.AC:
            return RepetitionCaseResult(
                verdict=run_result.verdict,
                total_wall_time_ms=total_wall_time_ms,
                peak_memory_kb=peak_memory_kb,
                peak_output_bytes=peak_output_bytes,
                peak_pids=peak_pids,
                exit_code=exit_code,
                exit_signal=exit_signal,
                stdout_excerpt=stdout_excerpt,
                stderr_excerpt=stderr_excerpt,
            )

    return RepetitionCaseResult(
        verdict=Verdict.AC,
        total_wall_time_ms=total_wall_time_ms,
        peak_memory_kb=peak_memory_kb,
        peak_output_bytes=peak_output_bytes,
        peak_pids=peak_pids,
        exit_code=exit_code,
        exit_signal=exit_signal,
        stdout_excerpt=stdout_excerpt,
        stderr_excerpt=stderr_excerpt,
    )


async def process_submission_job(
    submission: QueuedSubmission,
    db: DatabaseAccess,
    valkey: Valkey_Client,
    pool_manager: PoolManager,
    language_registry: dict[str, LanguageConfig],
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
    worker_id: str,
    attempt_token: str,
) -> None:
    """
    Execute the full compile → run → publish pipeline for one submission.

    Args:
        submission: Submission payload loaded from the database.
        db: Open worker database accessor.
        valkey: Async Valkey client for verdict publishing.
        pool_manager: Container pool manager.
        language_registry: Active language registry.
        docker_client: Synchronous Docker client.
        executor: ThreadPoolExecutor for Docker SDK calls.
        worker_id: Stable worker identity string.
        attempt_token: Attempt-scoped claim; every write below is fenced on it,
            so an attempt whose judgment was taken over writes nothing.

    Raises:
        JudgmentOwnershipLost: If another attempt claims the judgment mid-run.
    """
    from autojudge.runner import IsolateError  # avoid circular at module level

    container_id: str | None = None
    judgment_id = submission.judgment_id
    submission_id = submission.submission_id

    await db.set_judgment_dispatched(
        judgment_id,
        worker_id,
        attempt_token,
        contest_start_time=submission.contest_start_time,
    )

    try:
        language = get_language(language_registry, submission.language_id)
    except KeyError as exc:
        await db.set_judgment_failed(
            judgment_id,
            str(exc),
            contest_start_time=submission.contest_start_time,
            attempt_token=attempt_token,
        )
        return

    try:
        limits = await db.get_problem_limits(submission.problem_id, submission.language_id)
        prepared_validator = await prepare_custom_validator(
            domain="contest",
            judgment_id=judgment_id,
            problem_id=submission.problem_id,
            db=db,
            language_registry=language_registry,
            docker_client=docker_client,
            executor=executor,
        )
    except Exception as exc:
        await db.set_judgment_failed(
            judgment_id,
            f"Custom validator unavailable: {exc}",
            contest_start_time=submission.contest_start_time,
            attempt_token=attempt_token,
        )
        return
    if prepared_validator is not None and (
        not prepared_validator.compile_result.success or prepared_validator.compile_result.artifact_data is None
    ):
        await db.set_judgment_failed(
            judgment_id,
            f"Custom validator compilation failed: {prepared_validator.compile_result.compile_log}",
            contest_start_time=submission.contest_start_time,
            attempt_token=attempt_token,
        )
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
        await db.set_judgment_done(
            judgment_id,
            verdict=Verdict.CE,
            autojudge_only=submission.autojudge_only,
            attempt_token=attempt_token,
            contest_start_time=submission.contest_start_time,
            compile_log=compile_result.compile_log,
        )
        if submission.autojudge_only:
            await publish_verdict(
                valkey,
                VerdictEvent(
                    submission_id=submission_id,
                    judgment_id=judgment_id,
                    verdict=Verdict.CE.value,
                    compile_log=compile_result.compile_log,
                    contest_id=submission.contest_id,
                    team_id=submission.team_id,
                    problem_id=submission.problem_id,
                    update_kind="autojudge",
                ),
            )
        await invalidate_contest_result_caches(valkey, submission.contest_id)
        return

    await db.set_judgment_judging(judgment_id, attempt_token, contest_start_time=submission.contest_start_time)

    interactive_inputs: list[tuple[int, bytes]] = []
    per_language_limits: dict[str, ProblemLimits] | None = None
    if prepared_validator is not None:
        try:
            per_language_limits = await db.get_problem_effective_limits_by_language(submission.problem_id)
            interactive_inputs = _load_test_case_inputs(submission.problem_id)
        except (FileNotFoundError, LookupError, ValueError) as exc:
            await db.set_judgment_failed(
                judgment_id,
                str(exc),
                contest_start_time=submission.contest_start_time,
                attempt_token=attempt_token,
            )
            return

    interactive_result, _ = await run_custom_validator_submission(
        domain="contest",
        judgment_id=judgment_id,
        problem_id=submission.problem_id,
        contestant_language=language,
        contestant_artifact=compile_result.artifact_data or b"",
        limits=limits,
        test_cases=interactive_inputs,
        db=db,
        pool_manager=pool_manager,
        language_registry=language_registry,
        docker_client=docker_client,
        executor=executor,
        prepared=prepared_validator,
        user_language_id=submission.language_id,
        per_language_limits=per_language_limits,
        attempt_token=attempt_token,
    )
    if interactive_result is not None:
        verdict = interactive_result.classification.verdict
        if verdict is None:
            await db.set_judgment_failed(
                judgment_id,
                "Custom validator failed twice without a clean exit.",
                contest_start_time=submission.contest_start_time,
                attempt_token=attempt_token,
            )
            return
        await db.set_judgment_done(
            judgment_id,
            verdict=verdict,
            autojudge_only=submission.autojudge_only,
            attempt_token=attempt_token,
            contest_start_time=submission.contest_start_time,
            compile_log=compile_result.compile_log or None,
            max_wall_time_ms=interactive_result.wall_time_ms,
            max_memory_kb=interactive_result.memory_kb,
            min_wall_time_ms=interactive_result.wall_time_ms,
            min_memory_kb=interactive_result.memory_kb,
        )
        VERDICTS_TOTAL.labels(verdict=verdict.value, language_id=submission.language_id).inc()
        if interactive_result.wall_time_ms is not None:
            SUBMISSION_DURATION_SECONDS.labels(language_id=submission.language_id).observe(
                interactive_result.wall_time_ms / 1000
            )
        if submission.autojudge_only:
            await db.create_balloon_task_if_needed(submission, verdict)
            await publish_verdict(
                valkey,
                VerdictEvent(
                    submission_id=submission_id,
                    judgment_id=judgment_id,
                    verdict=verdict.value,
                    judge_time_ms=interactive_result.wall_time_ms,
                    contest_id=submission.contest_id,
                    team_id=submission.team_id,
                    problem_id=submission.problem_id,
                    update_kind="autojudge",
                ),
            )
        await invalidate_contest_result_caches(valkey, submission.contest_id)
        return

    try:
        test_cases = _load_test_cases(submission.problem_id)
    except (FileNotFoundError, ValueError) as exc:
        await db.set_judgment_failed(
            judgment_id,
            str(exc),
            contest_start_time=submission.contest_start_time,
            attempt_token=attempt_token,
        )
        return

    test_case_ids = await db.get_test_case_id_map(submission.problem_id)
    if len(test_case_ids) < len(test_cases):
        await db.set_judgment_failed(
            judgment_id,
            f"Filesystem/DB mismatch: {len(test_cases)} test files on disk "
            f"but only {len(test_case_ids)} test_case rows in DB for "
            f"problem '{submission.problem_id}'.",
            contest_start_time=submission.contest_start_time,
            attempt_token=attempt_token,
        )
        return

    try:
        container_id = await pool_manager.acquire(submission.language_id)
    except (PoolExhaustedError, PoolShutdownError) as exc:
        await db.set_judgment_failed(
            judgment_id,
            str(exc),
            contest_start_time=submission.contest_start_time,
            attempt_token=attempt_token,
        )
        return

    try:
        case_results: list[CaseResult] = []
        start_judge = time.monotonic()

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
                    "Recoverable isolate runtime failure while judging submission; "
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
                container_id = await pool_manager.acquire(submission.language_id)
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

            TEST_CASES_RUN_TOTAL.labels(language_id=submission.language_id).inc()
            await db.insert_test_result(
                judgment_id=judgment_id,
                test_case_id=test_case_ids[ordinal],
                verdict=repeated_result.verdict,
                wall_time_ms=repeated_result.total_wall_time_ms,
                memory_kb=repeated_result.peak_memory_kb,
                exit_code=repeated_result.exit_code,
                exit_signal=repeated_result.exit_signal,
                stdout_excerpt=repeated_result.stdout_excerpt,
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
            if repeated_result.verdict != Verdict.AC:
                break

        total_judge_ms = int((time.monotonic() - start_judge) * 1000)
        final_verdict = aggregate_verdict(case_results)
        resource_peak = worst_resource_usage(case_results)
        VERDICTS_TOTAL.labels(verdict=final_verdict.value, language_id=submission.language_id).inc()
        SUBMISSION_DURATION_SECONDS.labels(language_id=submission.language_id).observe(total_judge_ms / 1000)

        await db.set_judgment_done(
            judgment_id,
            verdict=final_verdict,
            autojudge_only=submission.autojudge_only,
            attempt_token=attempt_token,
            contest_start_time=submission.contest_start_time,
            compile_log=compile_result.compile_log or None,
            max_wall_time_ms=resource_peak["peak_wall_time_ms"],
            max_memory_kb=resource_peak["peak_memory_kb"],
            min_wall_time_ms=resource_peak["min_wall_time_ms"],
            min_memory_kb=resource_peak["min_memory_kb"],
        )
        if submission.autojudge_only:
            await db.create_balloon_task_if_needed(submission, final_verdict)
            await publish_verdict(
                valkey,
                VerdictEvent(
                    submission_id=submission_id,
                    judgment_id=judgment_id,
                    verdict=final_verdict.value,
                    judge_time_ms=total_judge_ms,
                    contest_id=submission.contest_id,
                    team_id=submission.team_id,
                    problem_id=submission.problem_id,
                    update_kind="autojudge",
                ),
            )
        await invalidate_contest_result_caches(valkey, submission.contest_id)
    except (PoolExhaustedError, PoolShutdownError) as exc:
        await db.set_judgment_failed(
            judgment_id,
            str(exc),
            contest_start_time=submission.contest_start_time,
            attempt_token=attempt_token,
        )
        return
    finally:
        if container_id is not None:
            with suppress(Exception):
                await pool_manager.release(container_id)
