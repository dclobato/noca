#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the non-scoring solution-test pipeline.

The load-bearing property is isolation: a completed solution test must write only
``solution_test_*`` rows, publish nothing on ``judge:results``, not invalidate the
scoreboard cache, and create no balloon task.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from autojudge import solution_test_job as solution_test_module
from autojudge.solution_test_job import process_solution_test_job
from autojudge.types import CompileResult, QueuedSolutionTestRun, RepetitionCaseResult
from shared.enumerations import Verdict


class _RecordingDb:
    """Records every solution-test transition the pipeline drives."""

    def __init__(self, *, test_case_ids: dict[int, str] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.case_results: list[dict[str, Any]] = []
        self._test_case_ids = test_case_ids if test_case_ids is not None else {1: "tc-1", 2: "tc-2", 3: "tc-3"}

    async def set_solution_test_dispatched(self, run_id: str, worker_id: str, attempt_token: str) -> None:
        self.calls.append(("dispatched", {"run_id": run_id, "worker_id": worker_id, "attempt_token": attempt_token}))

    async def set_solution_test_running(self, run_id: str, attempt_token: str) -> None:
        self.calls.append(("running", {"run_id": run_id, "attempt_token": attempt_token}))

    async def set_solution_test_failed(self, run_id: str, error_message: str, **kwargs: Any) -> None:
        self.calls.append(("failed", {"run_id": run_id, "error_message": error_message, **kwargs}))

    async def set_solution_test_done(self, run_id: str, **kwargs: Any) -> None:
        self.calls.append(("done", {"run_id": run_id, **kwargs}))

    async def insert_solution_test_case_result(self, **kwargs: Any) -> None:
        self.case_results.append(kwargs)

    async def get_problem_limits(self, problem_id: str, language_id: str) -> Any:
        from autojudge.types import ProblemLimits

        return ProblemLimits(time_limit_ms=1000, memory_limit_kb=65536, pids_limit=16)

    async def get_test_case_id_map(self, problem_id: str) -> dict[int, str]:
        return self._test_case_ids

    @property
    def outcome(self) -> tuple[str, dict[str, Any]]:
        """Return the terminal transition."""
        terminal = [call for call in self.calls if call[0] in {"done", "failed"}]
        assert terminal, f"pipeline never reached a terminal state: {self.calls}"
        return terminal[-1]


class _Pool:
    def __init__(self) -> None:
        self.released: list[str] = []

    async def acquire(self, language_id: str) -> str:
        return "container-1"

    async def release(self, container_id: str) -> None:
        self.released.append(container_id)


def _run(**overrides: Any) -> QueuedSolutionTestRun:
    payload = {
        "solution_test_run_id": "run-1",
        "contest_id": "contest-1",
        "problem_id": "problem-1",
        "language_id": "gcc-c17",
        "source_code": "int main(){}",
    }
    payload.update(overrides)
    return QueuedSolutionTestRun(**payload)  # type: ignore[arg-type]


def _patch_common(monkeypatch: pytest.MonkeyPatch, *, compile_success: bool = True) -> None:
    """Stub the compile phase, the validator probe, and the language registry."""

    async def _compile(*args: Any, **kwargs: Any) -> CompileResult:
        return CompileResult(
            success=compile_success,
            exit_code=0 if compile_success else 1,
            compile_log="" if compile_success else "syntax error",
            artifact_data=b"binary" if compile_success else None,
        )

    async def _no_validator(**kwargs: Any) -> None:
        return None

    monkeypatch.setattr(solution_test_module, "compile_submission", _compile)
    monkeypatch.setattr(solution_test_module, "prepare_custom_validator", _no_validator)
    monkeypatch.setattr(solution_test_module, "get_language", lambda registry, language_id: object())


def _patch_cases(monkeypatch: pytest.MonkeyPatch, verdicts: list[Verdict]) -> list[int]:
    """Run ``len(verdicts)`` cases, returning the ordinals actually executed."""
    executed: list[int] = []
    cases = [(f"in-{i}".encode(), f"out-{i}".encode()) for i in range(len(verdicts))]
    monkeypatch.setattr(solution_test_module, "_load_test_cases", lambda problem_id: cases)

    async def _repeat(**kwargs: Any) -> RepetitionCaseResult:
        ordinal = len(executed) + 1
        executed.append(ordinal)
        return RepetitionCaseResult(
            verdict=verdicts[ordinal - 1],
            total_wall_time_ms=10 * ordinal,
            peak_memory_kb=100 * ordinal,
            peak_output_bytes=5,
            peak_pids=1,
            exit_code=0,
            exit_signal=None,
            stdout_excerpt=b"",
            stderr_excerpt=b"",
        )

    monkeypatch.setattr(solution_test_module, "_run_repeated_test_case", _repeat)
    return executed


async def test_ordinary_problem_runs_every_case_and_aggregates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike profiling, an ordinary problem does not stop at the first non-AC case."""
    _patch_common(monkeypatch)
    executed = _patch_cases(monkeypatch, [Verdict.AC, Verdict.WA, Verdict.AC])
    db = _RecordingDb()

    await process_solution_test_job(_run(), db, _Pool(), {}, None, None, "worker-1", "worker-test:attempt-1")  # type: ignore[arg-type]

    assert executed == [1, 2, 3], "every test case must run, not just up to the first failure"
    kind, payload = db.outcome
    assert kind == "done"
    assert payload["verdict"] == Verdict.WA
    assert [row["ordinal"] for row in db.case_results] == [1, 2, 3]
    assert [row["input_excerpt"] for row in db.case_results] == [b"in-0", b"in-1", b"in-2"]
    assert [row["expected_output_excerpt"] for row in db.case_results] == [b"out-0", b"out-1", b"out-2"]
    # The accessor pins attempt_number to NULL for ordinary rows, so the caller
    # never passes it; that is what the "ordinary" partial unique index keys on.
    assert all("attempt_number" not in row for row in db.case_results)


async def test_case_detail_values_are_truncated_to_ten_kilobytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Input, expected output, and contestant output share the same hard ceiling."""
    _patch_common(monkeypatch)
    oversized_input = b"i" * (solution_test_module._CASE_DETAIL_MAX_BYTES + 100)
    oversized_expected = b"e" * (solution_test_module._CASE_DETAIL_MAX_BYTES + 100)
    oversized_output = b"o" * (solution_test_module._CASE_DETAIL_MAX_BYTES + 100)
    monkeypatch.setattr(
        solution_test_module,
        "_load_test_cases",
        lambda problem_id: [(oversized_input, oversized_expected)],
    )

    async def _repeat(**kwargs: Any) -> RepetitionCaseResult:
        return RepetitionCaseResult(
            verdict=Verdict.WA,
            total_wall_time_ms=10,
            peak_memory_kb=100,
            peak_output_bytes=len(oversized_output),
            peak_pids=1,
            exit_code=0,
            exit_signal=None,
            stdout_excerpt=oversized_output,
            stderr_excerpt=b"",
        )

    monkeypatch.setattr(solution_test_module, "_run_repeated_test_case", _repeat)
    db = _RecordingDb(test_case_ids={1: "tc-1"})

    await process_solution_test_job(_run(), db, _Pool(), {}, None, None, "worker-1", "worker-test:attempt-1")  # type: ignore[arg-type]

    row = db.case_results[0]
    for key in ("input_excerpt", "expected_output_excerpt", "stdout_excerpt"):
        value = row[key]
        assert len(value) == solution_test_module._CASE_DETAIL_MAX_BYTES
        assert value.endswith(solution_test_module._TRUNCATION_MARKER)


async def test_all_accepted_reports_ac(monkeypatch: pytest.MonkeyPatch) -> None:
    """A solution passing every case reports AC with the observed resource peaks."""
    _patch_common(monkeypatch)
    _patch_cases(monkeypatch, [Verdict.AC, Verdict.AC])
    db = _RecordingDb()

    await process_solution_test_job(_run(), db, _Pool(), {}, None, None, "worker-1", "worker-test:attempt-1")  # type: ignore[arg-type]

    kind, payload = db.outcome
    assert kind == "done"
    assert payload["verdict"] == Verdict.AC
    assert payload["max_wall_time_ms"] == 20
    assert payload["max_memory_kb"] == 200


async def test_compile_error_is_a_verdict_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A CE is the contestant's fault, so the run is DONE with verdict CE."""
    _patch_common(monkeypatch, compile_success=False)
    db = _RecordingDb()

    await process_solution_test_job(_run(), db, _Pool(), {}, None, None, "worker-1", "worker-test:attempt-1")  # type: ignore[arg-type]

    kind, payload = db.outcome
    assert kind == "done"
    assert payload["verdict"] == Verdict.CE
    assert payload["compile_log"] == "syntax error"
    assert db.case_results == []


async def test_filesystem_db_mismatch_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fewer test_case rows than files on disk is infrastructure failure, not a verdict."""
    _patch_common(monkeypatch)
    _patch_cases(monkeypatch, [Verdict.AC, Verdict.AC, Verdict.AC])
    db = _RecordingDb(test_case_ids={1: "tc-1"})

    await process_solution_test_job(_run(), db, _Pool(), {}, None, None, "worker-1", "worker-test:attempt-1")  # type: ignore[arg-type]

    kind, payload = db.outcome
    assert kind == "failed"
    assert "Filesystem/DB mismatch" in payload["error_message"]


async def test_retry_clears_prior_rows_via_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dispatch always runs first, and it is what makes a re-run idempotent."""
    _patch_common(monkeypatch)
    _patch_cases(monkeypatch, [Verdict.AC])
    db = _RecordingDb()

    await process_solution_test_job(_run(), db, _Pool(), {}, None, None, "worker-1", "worker-test:attempt-1")  # type: ignore[arg-type]

    assert db.calls[0][0] == "dispatched"


async def test_container_is_released(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pooled container is returned even on the happy path."""
    _patch_common(monkeypatch)
    _patch_cases(monkeypatch, [Verdict.AC])
    pool = _Pool()

    await process_solution_test_job(  # type: ignore[arg-type]
        _run(), _RecordingDb(), pool, {}, None, None, "worker-1", "worker-test:attempt-1"
    )

    assert pool.released == ["container-1"]


# ---------------------------------------------------------------------------
# Isolation sentinels
# ---------------------------------------------------------------------------


def test_processor_signature_has_no_valkey_handle() -> None:
    """Isolation is provable by signature, not only by assertion.

    Without a Valkey handle the processor *cannot* publish a verdict event or
    invalidate the scoreboard cache, however it is later edited.
    """
    parameters = set(inspect.signature(process_solution_test_job).parameters)
    assert "valkey" not in parameters


def test_module_imports_no_scoring_side_effects() -> None:
    """The module must not even reference the contest-scoring machinery."""
    source = inspect.getsource(solution_test_module)
    for forbidden in (
        "publish_verdict",
        "publish_arena_verdict",
        "publish_submission",
        "invalidate_scoreboard_cache",
        "create_balloon_task_if_needed",
        "VerdictEvent",
    ):
        assert forbidden not in source, f"{forbidden} must never be reachable from a solution test"


async def test_completed_run_touches_only_solution_test_accessors(monkeypatch: pytest.MonkeyPatch) -> None:
    """A completed run writes only solution-test rows.

    ``_RecordingDb`` implements *only* the solution-test accessors plus the two
    read-only problem lookups, so reaching for a judgment or balloon accessor
    would raise AttributeError and fail this test.
    """
    _patch_common(monkeypatch)
    _patch_cases(monkeypatch, [Verdict.AC, Verdict.WA])
    db = _RecordingDb()

    await process_solution_test_job(_run(), db, _Pool(), {}, None, None, "worker-1", "worker-test:attempt-1")  # type: ignore[arg-type]

    assert {call[0] for call in db.calls} <= {"dispatched", "running", "done", "failed"}
    assert db.outcome[0] == "done"


# ---------------------------------------------------------------------------
# Interactive (custom-validator) problems
# ---------------------------------------------------------------------------


async def _prepared_validator(**kwargs: Any) -> Any:
    """A validator that compiled cleanly, so the pipeline reaches the replay."""
    from autojudge.custom_validator_submission import PreparedCustomValidator

    return PreparedCustomValidator(
        language=object(),  # type: ignore[arg-type]
        compile_result=CompileResult(success=True, exit_code=0, compile_log="", artifact_data=b"validator"),
    )


def _patch_interactive(monkeypatch: pytest.MonkeyPatch, verdicts: list[Verdict]) -> dict[str, Any]:
    """Replay a validator whose Nth case yields ``verdicts[N-1]``.

    Stands in for ``run_custom_validator_submission`` while preserving its
    contract: it stops at the first case that does not end ``AC``, and reports
    that case's verdict plus the worst resources seen.
    """
    from autojudge.interactive_runner import InteractiveAttemptResult
    from autojudge.interactive_verdict import InteractiveVerdict

    captured: dict[str, Any] = {"executed": [], "attempt_target": None, "attempts": []}

    monkeypatch.setattr(solution_test_module, "prepare_custom_validator", _prepared_validator)
    monkeypatch.setattr(
        solution_test_module,
        "_load_test_case_inputs",
        lambda problem_id: [(index + 1, b"input") for index in range(len(verdicts))],
    )

    async def _replay(**kwargs: Any) -> tuple[Any, None]:
        captured["attempt_target"] = kwargs["attempt_target"]
        db = kwargs["db"]
        result = None
        for ordinal, _payload in kwargs["test_cases"]:
            captured["executed"].append(ordinal)
            verdict = verdicts[ordinal - 1]
            result = InteractiveAttemptResult(
                classification=InteractiveVerdict(verdict, False),
                contestant_exit_code=0,
                contestant_signal=None,
                validator_exit_code=0,
                validator_signal=None,
                transcript=None,
                contestant_stderr_excerpt=b"",
                validator_stderr_excerpt=b"",
                contestant_output_bytes=3,
                crash_reason=None,
                wall_time_ms=7 * ordinal,
                memory_kb=70 * ordinal,
            )
            await db.insert_interactive_attempt(
                domain="contest",
                owner_id=kwargs["judgment_id"],
                attempt_number=1,
                test_case_ordinal=ordinal,
                result=result,
                attempt_target=kwargs["attempt_target"],
            )
            captured["attempts"].append(ordinal)
            if verdict != Verdict.AC:
                break
        return result, None

    monkeypatch.setattr(solution_test_module, "run_custom_validator_submission", _replay)
    return captured


class _InteractiveDb(_RecordingDb):
    """Adds the accessors the interactive path needs."""

    def __init__(self) -> None:
        super().__init__()
        self.attempts: list[dict[str, Any]] = []

    async def get_problem_effective_limits_by_language(self, problem_id: str) -> dict[str, Any]:
        return {}

    async def insert_interactive_attempt(self, **kwargs: Any) -> None:
        self.attempts.append(kwargs)


async def test_interactive_problem_stops_at_the_first_non_ac_case(monkeypatch: pytest.MonkeyPatch) -> None:
    """The validator replay's own contract: the failing case's verdict is the run's."""
    _patch_common(monkeypatch)
    captured = _patch_interactive(monkeypatch, [Verdict.AC, Verdict.WA, Verdict.AC])
    db = _InteractiveDb()

    await process_solution_test_job(_run(), db, _Pool(), {}, None, None, "worker-1", "worker-test:attempt-1")  # type: ignore[arg-type]

    assert captured["executed"] == [1, 2], "iteration must stop at the first non-AC case"
    kind, payload = db.outcome
    assert kind == "done"
    assert payload["verdict"] == Verdict.WA
    assert payload["max_wall_time_ms"] == 14
    assert payload["max_memory_kb"] == 140


async def test_interactive_attempts_are_routed_to_the_solution_test_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`attempt_target` is what keeps staff attempts out of submission_interactive_attempts."""
    _patch_common(monkeypatch)
    captured = _patch_interactive(monkeypatch, [Verdict.AC])
    db = _InteractiveDb()

    await process_solution_test_job(_run(), db, _Pool(), {}, None, None, "worker-1", "worker-test:attempt-1")  # type: ignore[arg-type]

    assert captured["attempt_target"] == "solution_test"
    assert db.attempts and all(a["attempt_target"] == "solution_test" for a in db.attempts)
    # domain still selects the contest schema: a solution test runs against a contest problem.
    assert all(a["domain"] == "contest" for a in db.attempts)
    assert all(a["owner_id"] == "run-1" for a in db.attempts)
    # Ordinary case rows are never written on the interactive path.
    assert db.case_results == []


async def test_interactive_crash_without_verdict_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """A validator that never exits cleanly is infrastructure failure, not a verdict."""
    _patch_common(monkeypatch)

    from autojudge.interactive_runner import InteractiveAttemptResult
    from autojudge.interactive_verdict import InteractiveVerdict

    async def _replay(**kwargs: Any) -> tuple[Any, None]:
        return (
            InteractiveAttemptResult(
                classification=InteractiveVerdict(None, True),
                contestant_exit_code=None,
                contestant_signal=None,
                validator_exit_code=None,
                validator_signal=None,
                transcript=None,
                contestant_stderr_excerpt=b"",
                validator_stderr_excerpt=b"boom",
                contestant_output_bytes=0,
                crash_reason=None,
            ),
            None,
        )

    monkeypatch.setattr(solution_test_module, "prepare_custom_validator", _prepared_validator)
    monkeypatch.setattr(solution_test_module, "_load_test_case_inputs", lambda problem_id: [(1, b"input")])
    monkeypatch.setattr(solution_test_module, "run_custom_validator_submission", _replay)
    db = _InteractiveDb()

    await process_solution_test_job(_run(), db, _Pool(), {}, None, None, "worker-1", "worker-test:attempt-1")  # type: ignore[arg-type]

    kind, payload = db.outcome
    assert kind == "failed"
    assert "without a clean exit" in payload["error_message"]


async def test_runtime_error_is_reported_as_a_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """A contestant crash is the contestant's fault: RE is a verdict, not a judge failure."""
    _patch_common(monkeypatch)
    executed = _patch_cases(monkeypatch, [Verdict.AC, Verdict.RE])
    db = _RecordingDb()

    await process_solution_test_job(_run(), db, _Pool(), {}, None, None, "worker-1", "worker-test:attempt-1")  # type: ignore[arg-type]

    assert executed == [1, 2]
    kind, payload = db.outcome
    assert kind == "done", "a crashed contestant program must not mark the run FAILED"
    assert payload["verdict"] == Verdict.RE
    assert [row["verdict"] for row in db.case_results] == [Verdict.AC, Verdict.RE]


async def test_infrastructure_failure_is_not_a_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exhausted container pool is the judge's fault, so no verdict is invented."""
    _patch_common(monkeypatch)
    _patch_cases(monkeypatch, [Verdict.AC])

    from autojudge.pool import PoolExhaustedError

    class _ExhaustedPool(_Pool):
        async def acquire(self, language_id: str) -> str:
            raise PoolExhaustedError("no containers available")

    db = _RecordingDb()

    await process_solution_test_job(  # type: ignore[arg-type]
        _run(), db, _ExhaustedPool(), {}, None, None, "worker-1", "worker-test:attempt-1"
    )

    kind, payload = db.outcome
    assert kind == "failed"
    assert "no containers available" in payload["error_message"]
    assert db.case_results == []


async def test_unknown_language_fails_before_compiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """A language dropped from the registry cannot produce a verdict."""
    _patch_common(monkeypatch)

    def _missing(registry: Any, language_id: str) -> Any:
        raise KeyError(f"Unknown language '{language_id}'")

    monkeypatch.setattr(solution_test_module, "get_language", _missing)
    db = _RecordingDb()

    await process_solution_test_job(_run(), db, _Pool(), {}, None, None, "worker-1", "worker-test:attempt-1")  # type: ignore[arg-type]

    kind, payload = db.outcome
    assert kind == "failed"
    assert "Unknown language" in payload["error_message"]


async def test_unavailable_custom_validator_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured validator with no active VALID revision blocks the run."""
    _patch_common(monkeypatch)

    from autojudge.custom_validator_submission import CustomValidatorUnavailableError

    async def _unavailable(**kwargs: Any) -> None:
        raise CustomValidatorUnavailableError("The configured custom validator has no active valid revision.")

    monkeypatch.setattr(solution_test_module, "prepare_custom_validator", _unavailable)
    db = _RecordingDb()

    await process_solution_test_job(_run(), db, _Pool(), {}, None, None, "worker-1", "worker-test:attempt-1")  # type: ignore[arg-type]

    kind, payload = db.outcome
    assert kind == "failed"
    assert "Custom validator unavailable" in payload["error_message"]


async def test_validator_that_fails_to_compile_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken validator is the problem author's fault, never the contestant's CE."""
    _patch_common(monkeypatch)

    async def _broken(**kwargs: Any) -> Any:
        from autojudge.custom_validator_submission import PreparedCustomValidator

        return PreparedCustomValidator(
            language=object(),  # type: ignore[arg-type]
            compile_result=CompileResult(
                success=False, exit_code=1, compile_log="validator.c:1: error", artifact_data=None
            ),
        )

    monkeypatch.setattr(solution_test_module, "prepare_custom_validator", _broken)
    db = _RecordingDb()

    await process_solution_test_job(_run(), db, _Pool(), {}, None, None, "worker-1", "worker-test:attempt-1")  # type: ignore[arg-type]

    kind, payload = db.outcome
    assert kind == "failed"
    assert "Custom validator compilation failed" in payload["error_message"]
    assert payload.get("verdict") is None
