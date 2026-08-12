#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import CheckConstraint, Table

from autojudge import custom_validator_validation as validation
from autojudge.db._custom_validator import _CustomValidatorMixin
from autojudge.interactive_transcript import InteractiveTranscript, TranscriptLine
from autojudge.types import CompileResult
from shared.db_schema import arena_submission_interactive_attempts, submission_interactive_attempts
from shared.enumerations import (
    CustomValidatorActiveState,
    CustomValidatorCandidateState,
    CustomValidatorCrashReason,
    Verdict,
)
from shared.language_registry import default_language_registry


@pytest.mark.parametrize(
    ("table", "constraint_name"),
    [
        (submission_interactive_attempts, "ck_submission_limit_outcome"),
        (arena_submission_interactive_attempts, "ck_arena_limit_outcome"),
    ],
)
def test_interactive_limit_outcome_constraints_allow_tle(table: Table, constraint_name: str) -> None:
    """Both submission domains must persist contestant-attributed TLE diagnostics."""
    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert "'TLE'" in constraints[constraint_name]


def _connection(row: dict[str, object] | None) -> Mock:
    selected = Mock()
    selected.mappings.return_value.one_or_none.return_value = row
    connection = Mock()
    connection.execute = AsyncMock(side_effect=[selected, Mock()])
    connection.commit = AsyncMock()
    return connection


def _pending_row() -> dict[str, object]:
    return {
        "candidate_language_id": "python3",
        "candidate_source": "print('validator')",
    }


@pytest.mark.asyncio
async def test_stale_validation_token_is_noop(monkeypatch) -> None:
    connection = _connection(None)
    compile_submission = AsyncMock()
    monkeypatch.setattr(validation, "compile_submission", compile_submission)

    await validation.process_custom_validator_validation(
        validation_id="stale",
        domain="contest",
        problem_id="problem",
        candidate_token="stale",
        connection=connection,
        language_registry=default_language_registry(),
        docker_client=Mock(),
        executor=Mock(),
    )

    compile_submission.assert_not_awaited()
    connection.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_language_rejects_matching_candidate() -> None:
    row = _pending_row()
    row["candidate_language_id"] = "removed-language"
    connection = _connection(row)

    await validation.process_custom_validator_validation(
        validation_id="token",
        domain="arena",
        problem_id="problem",
        candidate_token="token",
        connection=connection,
        language_registry=default_language_registry(),
        docker_client=Mock(),
        executor=Mock(),
    )

    params = connection.execute.await_args_list[1].args[0].compile().params
    assert params["candidate_state"] == CustomValidatorCandidateState.INVALID
    assert "Unknown or inactive" in params["candidate_compile_log"]
    connection.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_compile_failure_retains_invalid_candidate_and_log(monkeypatch) -> None:
    connection = _connection(_pending_row())
    monkeypatch.setattr(
        validation,
        "compile_submission",
        AsyncMock(return_value=CompileResult(False, 1, "compiler failed", None)),
    )

    await validation.process_custom_validator_validation(
        validation_id="token",
        domain="contest",
        problem_id="problem",
        candidate_token="token",
        connection=connection,
        language_registry=default_language_registry(),
        docker_client=Mock(),
        executor=Mock(),
    )

    params = connection.execute.await_args_list[1].args[0].compile().params
    assert params["candidate_state"] == CustomValidatorCandidateState.INVALID
    assert params["candidate_compile_log"] == "compiler failed"


@pytest.mark.asyncio
async def test_compile_success_promotes_and_clears_candidate(monkeypatch) -> None:
    connection = _connection(_pending_row())
    monkeypatch.setattr(
        validation,
        "compile_submission",
        AsyncMock(return_value=CompileResult(True, 0, "", b"artifact")),
    )

    await validation.process_custom_validator_validation(
        validation_id="token",
        domain="arena",
        problem_id="problem",
        candidate_token="token",
        connection=connection,
        language_registry=default_language_registry(),
        docker_client=Mock(),
        executor=Mock(),
    )

    params = connection.execute.await_args_list[1].args[0].compile().params
    assert params["active_state"] == CustomValidatorActiveState.VALID
    assert params["active_source"] == "print('validator')"
    assert params["candidate_source"] is None
    assert params["candidate_token"] is None


@pytest.mark.asyncio
async def test_interactive_attempt_insert_replaces_stale_rows() -> None:
    connection = Mock()
    connection.execute = AsyncMock()
    connection.commit = AsyncMock()
    db = _CustomValidatorMixin(connection)
    result = SimpleNamespace(
        crash_reason=None,
        validator_exit_code=0,
        classification=SimpleNamespace(verdict=Verdict.AC),
        contestant_exit_code=0,
        contestant_signal=None,
        validator_signal=None,
        transcript=InteractiveTranscript(
            lines=[
                TranscriptLine(direction="user", line="7"),
                TranscriptLine(direction="validator", line="ok"),
            ],
            truncated=False,
        ),
        contestant_stderr_excerpt=b"",
        validator_stderr_excerpt=b"",
        wall_time_ms=10,
        memory_kb=100,
        contestant_output_bytes=5,
        watchdog_stalled_side=None,
    )

    await db.insert_interactive_attempt(
        domain="contest",
        owner_id="judgment-1",
        attempt_number=1,
        result=result,
    )

    first_statement = connection.execute.await_args_list[0].args[0]
    second_statement = connection.execute.await_args_list[1].args[0]
    assert first_statement.is_delete
    assert second_statement.is_insert
    assert connection.commit.await_count == 1

    values = second_statement.compile().params
    assert values["transcript"] == {
        "lines": [{"dir": "user", "line": "7"}, {"dir": "validator", "line": "ok"}],
        "truncated": False,
    }
    assert "contestant_stdout_excerpt" not in values
    assert "validator_stdout_excerpt" not in values


@pytest.mark.asyncio
async def test_interactive_attempt_insert_accepts_missing_transcript() -> None:
    """A startup/watchdog failure never ran the bridge, so it has no conversation."""
    connection = Mock()
    connection.execute = AsyncMock()
    connection.commit = AsyncMock()
    db = _CustomValidatorMixin(connection)
    result = SimpleNamespace(
        crash_reason=CustomValidatorCrashReason.STARTUP,
        validator_exit_code=None,
        classification=SimpleNamespace(verdict=None),
        contestant_exit_code=None,
        contestant_signal=None,
        validator_signal=None,
        transcript=None,
        contestant_stderr_excerpt=b"",
        validator_stderr_excerpt=b"boom",
        wall_time_ms=None,
        memory_kb=None,
        contestant_output_bytes=0,
        watchdog_stalled_side=None,
    )

    await db.insert_interactive_attempt(
        domain="arena",
        owner_id="judgment-2",
        attempt_number=1,
        result=result,
    )

    values = connection.execute.await_args_list[1].args[0].compile().params
    assert values["transcript"] is None
    assert values["crash_reason"] == CustomValidatorCrashReason.STARTUP


@pytest.mark.asyncio
async def test_contestant_watchdog_stall_persists_as_tle_limit_outcome() -> None:
    connection = Mock()
    connection.execute = AsyncMock()
    connection.commit = AsyncMock()
    db = _CustomValidatorMixin(connection)
    result = SimpleNamespace(
        crash_reason=None,
        validator_exit_code=None,
        classification=SimpleNamespace(verdict=Verdict.TLE),
        contestant_exit_code=None,
        contestant_signal=None,
        validator_signal=None,
        transcript=InteractiveTranscript(
            lines=[TranscriptLine(direction="validator", line="53002399")],
            truncated=False,
        ),
        contestant_stderr_excerpt=b"",
        validator_stderr_excerpt=b"",
        wall_time_ms=None,
        memory_kb=None,
        contestant_output_bytes=0,
        watchdog_stalled_side="contestant",
    )

    await db.insert_interactive_attempt(
        domain="arena",
        owner_id="judgment-3",
        attempt_number=1,
        result=result,
    )

    values = connection.execute.await_args_list[1].args[0].compile().params
    assert values["limit_outcome"] == Verdict.TLE.value
    assert values["validator_verdict"] is None
    assert values["crash_reason"] is None
