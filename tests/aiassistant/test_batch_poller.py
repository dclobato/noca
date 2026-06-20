#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for aiassistant.batch_poller.

All database tests use the shared SQLite engine fixture from conftest.py.
OpenAI calls are always mocked — no real API calls are made.
"""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa

from aiassistant import batch_poller, batch_status
from aiassistant.db.batch_queries import BatchJobRow, get_pending_batch_jobs
from shared.db_schema import (
    arena_ai_batch_jobs,
    arena_notifications,
    arena_problems,
    arena_submission_ai_reviews,
    arena_submissions,
    arena_users,
    languages,
)
from shared.enumerations import ArenaAIBatchJobStatus, ArenaNotificationKind, ArenaRole
from shared.services.valkey_service import AI_BATCH_TURNAROUND_STATS_KEY, ValkeyRuntime

# ---------------------------------------------------------------------------
# DB seed helpers
# ---------------------------------------------------------------------------

_LANGUAGE_ID = "python3"
_USER_ID = "poller-user-1"
_PROBLEM_ID = "poller-problem-1"
_SUBMISSION_ID = "poller-submission-1"
_OPENAI_BATCH_ID = "batch-poller-001"


async def _seed_submission(
    engine: object,
    *,
    submission_id: str = _SUBMISSION_ID,
    user_id: str = _USER_ID,
    problem_id: str = _PROBLEM_ID,
    language_id: str = _LANGUAGE_ID,
) -> None:
    """Insert the minimal Arena graph (language + user + problem + submission)."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        # language (ignore conflicts — fixture may have already inserted it)
        with contextlib.suppress(Exception):
            await conn.execute(
                languages.insert().values(
                    id=language_id,
                    name="Python 3",
                    icon="python",
                    compile_image="noca/python:compile",
                    run_image="noca/python:run",
                    compile_cmd=None,
                    run_cmd=["python3", "main.py"],
                    source_filename="main.py",
                    artifact_path="/sandbox/main.py",
                    artifact_is_source=True,
                    compile_timeout_s=10.0,
                    profiling_repetitions_default=3,
                    profiled_pids_floor=32,
                    active=True,
                )
            )

        await conn.execute(
            arena_users.insert().values(
                id=user_id,
                nome="Poller Test User",
                dta_nascimento=date(1995, 1, 1),
                email_normalizado=f"{user_id}@test.example.com",
                password_hash="hash",
                role=ArenaRole.ARENA_USER.value,
                _ai_api_key=None,
            )
        )
        await conn.execute(
            arena_problems.insert().values(
                id=problem_id,
                arena_number=1,
                title="Poller Problem",
                owner_id=user_id,
                problem_statement="# Statement\nSolve it.",
                enabled=True,
                problem_image_base64=None,
                problem_image_mime=None,
                problem_image_caption=None,
            )
        )
        await conn.execute(
            arena_submissions.insert().values(
                id=submission_id,
                user_id=user_id,
                problem_id=problem_id,
                language_id=language_id,
                source_code="print('hello')",
                source_hash="a" * 64,
                source_size_bytes=14,
                submit_to_ai=True,
            )
        )


async def _insert_batch_job(
    engine: object,
    *,
    submission_id: str = _SUBMISSION_ID,
    openai_batch_id: str = _OPENAI_BATCH_ID,
    local_status: str = ArenaAIBatchJobStatus.SUBMITTED.value,
    input_file_id: str = "file-input-001",
    code_file_id: str = "file-code-001",
    statement_file_id: str = "file-stmt-001",
) -> None:
    """Insert an arena_ai_batch_jobs row directly (bypassing domain helper)."""
    import uuid

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        await conn.execute(
            arena_ai_batch_jobs.insert().values(
                id=str(uuid.uuid4()),
                submission_id=submission_id,
                openai_batch_id=openai_batch_id,
                local_status=local_status,
                input_file_id=input_file_id,
                code_file_id=code_file_id,
                statement_file_id=statement_file_id,
            )
        )


async def _fetch_batch_job(engine: object, openai_batch_id: str = _OPENAI_BATCH_ID) -> sa.RowMapping | None:
    """Return the batch job row for the given openai_batch_id."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    sa.select(arena_ai_batch_jobs).where(arena_ai_batch_jobs.c.openai_batch_id == openai_batch_id)
                )
            )
            .mappings()
            .first()
        )
    return row


async def _fetch_ai_review(engine: object, submission_id: str = _SUBMISSION_ID) -> sa.RowMapping | None:
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    sa.select(arena_submission_ai_reviews).where(
                        arena_submission_ai_reviews.c.submission_id == submission_id
                    )
                )
            )
            .mappings()
            .first()
        )
    return row


async def _fetch_notification(
    engine: object,
    submission_id: str = _SUBMISSION_ID,
    kind: str = ArenaNotificationKind.AI_REVIEW_COMPLETED.value,
) -> sa.RowMapping | None:
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    sa.select(arena_notifications).where(
                        arena_notifications.c.source_ref == submission_id,
                        arena_notifications.c.notification_kind == kind,
                    )
                )
            )
            .mappings()
            .first()
        )
    return row


async def _fetch_submit_to_ai_flag(engine: object, submission_id: str = _SUBMISSION_ID) -> bool | None:
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    sa.select(arena_submissions.c.submit_to_ai).where(arena_submissions.c.id == submission_id)
                )
            )
            .mappings()
            .first()
        )
    return bool(row["submit_to_ai"]) if row is not None else None


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_batch_obj(
    status: str,
    output_file_id: str | None = None,
    error_file_id: str | None = None,
    request_counts_total: int = 1,
    request_counts_completed: int = 1,
    request_counts_failed: int = 0,
    batch_errors: object = None,
) -> MagicMock:
    """Build a minimal mock OpenAI batch object."""
    b = MagicMock()
    b.status = status
    b.output_file_id = output_file_id
    b.error_file_id = error_file_id
    rc = MagicMock()
    rc.total = request_counts_total
    rc.completed = request_counts_completed
    rc.failed = request_counts_failed
    b.request_counts = rc
    b.errors = batch_errors
    return b


def _make_output_jsonl(
    submission_id: str = _SUBMISSION_ID,
    text: str = "Great attempt! Check your loop boundary.",
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> str:
    """Return a single JSONL line matching the Responses API batch output format."""
    record = {
        "custom_id": submission_id,
        "response": {
            "status_code": 200,
            "body": {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": text}],
                    }
                ],
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            },
        },
    }
    return json.dumps(record)


def _make_error_jsonl(submission_id: str = _SUBMISSION_ID, message: str = "Per-request error") -> str:
    """Return a single JSONL line for a per-request error."""
    record = {
        "custom_id": submission_id,
        "error": {"message": message},
    }
    return json.dumps(record)


def _make_mock_client(
    batch: MagicMock,
    output_text: str | None = None,
    error_text: str | None = None,
) -> MagicMock:
    """Build a mock AsyncOpenAI client for poller tests."""
    client = MagicMock()
    client.batches.retrieve = AsyncMock(return_value=batch)

    # files.content returns an object with .text
    def _make_file_content(text: str) -> MagicMock:
        content = MagicMock()
        content.text = text
        return content

    async def _files_content(file_id: str) -> MagicMock:
        if output_text is not None and file_id == batch.output_file_id:
            return _make_file_content(output_text)
        if error_text is not None and file_id == batch.error_file_id:
            return _make_file_content(error_text)
        return _make_file_content("")

    client.files.content = _files_content
    client.files.delete = AsyncMock(return_value=None)
    return client


_LOG = logging.getLogger("test_batch_poller")


def _make_valkey_runtime() -> MagicMock:
    """Return a mock Valkey runtime for poll-cycle tests."""
    runtime = MagicMock(spec=ValkeyRuntime)
    runtime.set = AsyncMock()
    runtime.delete = AsyncMock()
    return runtime


# ---------------------------------------------------------------------------
# Pure-function unit tests (no DB, no network)
# ---------------------------------------------------------------------------


def test_extract_output_text_happy_path() -> None:
    """Extracts text from a valid Responses API output body."""
    body = {
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "Good try!"}]},
        ]
    }
    assert batch_status.extract_output_text(body) == "Good try!"


def test_extract_output_text_skips_non_message_types() -> None:
    """Returns None when no message-type output item is present."""
    body = {
        "output": [
            {"type": "tool_call", "content": [{"type": "output_text", "text": "ignored"}]},
        ]
    }
    assert batch_status.extract_output_text(body) is None


def test_extract_output_text_empty_output() -> None:
    """Returns None for an empty output list."""
    assert batch_status.extract_output_text({"output": []}) is None


def test_extract_output_text_missing_output_key() -> None:
    """Returns None when the output key is absent."""
    assert batch_status.extract_output_text({}) is None


def test_extract_output_text_skips_non_output_text_content() -> None:
    """Returns None when no output_text content item exists in a message."""
    body = {
        "output": [
            {"type": "message", "content": [{"type": "input_text", "text": "not output"}]},
        ]
    }
    assert batch_status.extract_output_text(body) is None


def test_openai_status_to_local_completed() -> None:
    assert batch_status.openai_status_to_local("completed") == ArenaAIBatchJobStatus.COMPLETED.value


def test_openai_status_to_local_failed() -> None:
    assert batch_status.openai_status_to_local("failed") == ArenaAIBatchJobStatus.FAILED.value


def test_openai_status_to_local_expired() -> None:
    assert batch_status.openai_status_to_local("expired") == ArenaAIBatchJobStatus.EXPIRED.value


def test_openai_status_to_local_cancelled() -> None:
    assert batch_status.openai_status_to_local("cancelled") == ArenaAIBatchJobStatus.CANCELLED.value


def test_openai_status_to_local_cancelling_maps_to_cancelled() -> None:
    assert batch_status.openai_status_to_local("cancelling") == ArenaAIBatchJobStatus.CANCELLED.value


def test_openai_status_to_local_unknown_maps_to_failed() -> None:
    """Unknown/unexpected statuses fall back to FAILED to avoid silent data loss."""
    assert batch_status.openai_status_to_local("bogus") == ArenaAIBatchJobStatus.FAILED.value


def test_extract_batch_error_returns_first_message() -> None:
    """Extracts the first error message from a batch errors object."""
    err_item = MagicMock()
    err_item.message = "Something went wrong"
    errors = MagicMock()
    errors.data = [err_item]
    batch = MagicMock()
    batch.errors = errors
    assert batch_status.extract_batch_error(batch) == "Something went wrong"


def test_extract_batch_error_returns_none_when_no_errors() -> None:
    batch = MagicMock()
    batch.errors = None
    assert batch_status.extract_batch_error(batch) is None


def test_extract_batch_error_returns_none_for_empty_data() -> None:
    errors = MagicMock()
    errors.data = []
    batch = MagicMock()
    batch.errors = errors
    assert batch_status.extract_batch_error(batch) is None


# ---------------------------------------------------------------------------
# _process_batch — non-terminal status (no finalization)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_batch_in_progress_updates_poll_fields(engine: object) -> None:
    """Non-terminal batch updates openai_status and last_polled_at but does NOT finalize."""
    await _seed_submission(engine)
    await _insert_batch_job(engine)

    job = BatchJobRow(
        id="job-id",
        submission_id=_SUBMISSION_ID,
        openai_batch_id=_OPENAI_BATCH_ID,
        local_status=ArenaAIBatchJobStatus.SUBMITTED.value,
        input_file_id="file-input-001",
        code_file_id="file-code-001",
        statement_file_id="file-stmt-001",
        error_file_id=None,
    )

    batch = _make_batch_obj("in_progress")
    client = _make_mock_client(batch)

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]

    await batch_poller._process_batch(db_engine, client, job.openai_batch_id, [job], _LOG)

    row = await _fetch_batch_job(engine)
    assert row is not None
    assert row["openai_status"] == "in_progress"
    assert row["local_status"] == ArenaAIBatchJobStatus.POLLING.value
    assert row["last_polled_at"] is not None
    # Not finalized — completed_at stays None
    assert row["completed_at"] is None


@pytest.mark.asyncio
async def test_process_batch_in_progress_does_not_store_review(engine: object) -> None:
    """No AI review row created while batch is still in progress."""
    await _seed_submission(engine)
    await _insert_batch_job(engine)

    job = BatchJobRow(
        id="job-id",
        submission_id=_SUBMISSION_ID,
        openai_batch_id=_OPENAI_BATCH_ID,
        local_status=ArenaAIBatchJobStatus.SUBMITTED.value,
        input_file_id="file-input-001",
        code_file_id="file-code-001",
        statement_file_id="file-stmt-001",
        error_file_id=None,
    )
    batch = _make_batch_obj("in_progress")
    client = _make_mock_client(batch)
    client.files.delete = AsyncMock()

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]

    await batch_poller._process_batch(db_engine, client, job.openai_batch_id, [job], _LOG)

    assert await _fetch_ai_review(engine) is None
    client.files.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# _process_batch — completed status (happy path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_batch_completed_stores_review_result(engine: object) -> None:
    """Completed batch stores the AI review text in arena_submission_ai_reviews."""
    await _seed_submission(engine)
    await _insert_batch_job(engine)

    output_text = _make_output_jsonl(text="Think about off-by-one errors.")
    batch = _make_batch_obj("completed", output_file_id="file-out-001")
    client = _make_mock_client(batch, output_text=output_text)

    job = BatchJobRow(
        id="job-id",
        submission_id=_SUBMISSION_ID,
        openai_batch_id=_OPENAI_BATCH_ID,
        local_status=ArenaAIBatchJobStatus.SUBMITTED.value,
        input_file_id="file-input-001",
        code_file_id="file-code-001",
        statement_file_id="file-stmt-001",
        error_file_id=None,
    )

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    with (
        patch.object(batch_poller.settings, "OPENAI_INPUT_TOKEN_PRICE", 1.0),
        patch.object(batch_poller.settings, "OPENAI_OUTPUT_TOKEN_PRICE", 2.0),
        patch.object(batch_poller.settings, "OPENAI_BATCH_INPUT_TOKEN_PRICE", None),
        patch.object(batch_poller.settings, "OPENAI_BATCH_OUTPUT_TOKEN_PRICE", None),
    ):
        await batch_poller._process_batch(db_engine, client, job.openai_batch_id, [job], _LOG)

    review = await _fetch_ai_review(engine)
    assert review is not None
    assert review["ai_response"] == "Think about off-by-one errors."
    assert review["used_platform_key"] is True


@pytest.mark.asyncio
async def test_process_batch_completed_sends_notification(engine: object) -> None:
    """Completed batch sends an AI_REVIEW_COMPLETED notification."""
    await _seed_submission(engine)
    await _insert_batch_job(engine)

    output_text = _make_output_jsonl()
    batch = _make_batch_obj("completed", output_file_id="file-out-001")
    client = _make_mock_client(batch, output_text=output_text)

    job = BatchJobRow(
        id="job-id",
        submission_id=_SUBMISSION_ID,
        openai_batch_id=_OPENAI_BATCH_ID,
        local_status=ArenaAIBatchJobStatus.SUBMITTED.value,
        input_file_id="file-input-001",
        code_file_id="file-code-001",
        statement_file_id="file-stmt-001",
        error_file_id=None,
    )

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    with (
        patch.object(batch_poller.settings, "OPENAI_INPUT_TOKEN_PRICE", 1.0),
        patch.object(batch_poller.settings, "OPENAI_OUTPUT_TOKEN_PRICE", 2.0),
        patch.object(batch_poller.settings, "OPENAI_BATCH_INPUT_TOKEN_PRICE", None),
        patch.object(batch_poller.settings, "OPENAI_BATCH_OUTPUT_TOKEN_PRICE", None),
    ):
        await batch_poller._process_batch(db_engine, client, job.openai_batch_id, [job], _LOG)

    notif = await _fetch_notification(engine, kind=ArenaNotificationKind.AI_REVIEW_COMPLETED.value)
    assert notif is not None


@pytest.mark.asyncio
async def test_process_batch_completed_sets_local_status(engine: object) -> None:
    """Completed batch finalizes local_status to 'completed'."""
    await _seed_submission(engine)
    await _insert_batch_job(engine)

    output_text = _make_output_jsonl()
    batch = _make_batch_obj("completed", output_file_id="file-out-001")
    client = _make_mock_client(batch, output_text=output_text)

    job = BatchJobRow(
        id="job-id",
        submission_id=_SUBMISSION_ID,
        openai_batch_id=_OPENAI_BATCH_ID,
        local_status=ArenaAIBatchJobStatus.SUBMITTED.value,
        input_file_id="file-input-001",
        code_file_id="file-code-001",
        statement_file_id="file-stmt-001",
        error_file_id=None,
    )

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    with (
        patch.object(batch_poller.settings, "OPENAI_INPUT_TOKEN_PRICE", 1.0),
        patch.object(batch_poller.settings, "OPENAI_OUTPUT_TOKEN_PRICE", 2.0),
        patch.object(batch_poller.settings, "OPENAI_BATCH_INPUT_TOKEN_PRICE", None),
        patch.object(batch_poller.settings, "OPENAI_BATCH_OUTPUT_TOKEN_PRICE", None),
    ):
        await batch_poller._process_batch(db_engine, client, job.openai_batch_id, [job], _LOG)

    row = await _fetch_batch_job(engine)
    assert row is not None
    assert row["local_status"] == ArenaAIBatchJobStatus.COMPLETED.value
    assert row["completed_at"] is not None


@pytest.mark.asyncio
async def test_process_batch_completed_deletes_openai_files(engine: object) -> None:
    """All three uploaded OpenAI files are deleted after a completed batch."""
    await _seed_submission(engine)
    await _insert_batch_job(engine)

    output_text = _make_output_jsonl()
    batch = _make_batch_obj("completed", output_file_id="file-out-001")
    client = _make_mock_client(batch, output_text=output_text)

    job = BatchJobRow(
        id="job-id",
        submission_id=_SUBMISSION_ID,
        openai_batch_id=_OPENAI_BATCH_ID,
        local_status=ArenaAIBatchJobStatus.SUBMITTED.value,
        input_file_id="file-input-001",
        code_file_id="file-code-001",
        statement_file_id="file-stmt-001",
        error_file_id=None,
    )

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    with (
        patch.object(batch_poller.settings, "OPENAI_INPUT_TOKEN_PRICE", 1.0),
        patch.object(batch_poller.settings, "OPENAI_OUTPUT_TOKEN_PRICE", 2.0),
        patch.object(batch_poller.settings, "OPENAI_BATCH_INPUT_TOKEN_PRICE", None),
        patch.object(batch_poller.settings, "OPENAI_BATCH_OUTPUT_TOKEN_PRICE", None),
    ):
        await batch_poller._process_batch(db_engine, client, job.openai_batch_id, [job], _LOG)

    deleted_ids = {call.args[0] for call in client.files.delete.await_args_list}
    assert "file-input-001" in deleted_ids
    assert "file-code-001" in deleted_ids
    assert "file-stmt-001" in deleted_ids


@pytest.mark.asyncio
async def test_process_batch_completed_records_cost(engine: object) -> None:
    """Cost is computed from per-line usage and stored as cost_micros."""
    await _seed_submission(engine)
    await _insert_batch_job(engine)

    # 100 input tokens at $0.5/1M (= OPENAI_INPUT_TOKEN_PRICE/2) + 50 output at $1/1M
    # effective_batch_input_price = 1.0 / 2 = 0.5 per 1M
    # effective_batch_output_price = 2.0 / 2 = 1.0 per 1M
    # cost = (100/1e6)*0.5 + (50/1e6)*1.0 = 0.00005 + 0.00005 = 0.0001
    # cost_micros = round(0.0001 * 1e6) = 100
    output_text = _make_output_jsonl(input_tokens=100, output_tokens=50)
    batch = _make_batch_obj("completed", output_file_id="file-out-001")
    client = _make_mock_client(batch, output_text=output_text)

    job = BatchJobRow(
        id="job-id",
        submission_id=_SUBMISSION_ID,
        openai_batch_id=_OPENAI_BATCH_ID,
        local_status=ArenaAIBatchJobStatus.SUBMITTED.value,
        input_file_id="file-input-001",
        code_file_id="file-code-001",
        statement_file_id="file-stmt-001",
        error_file_id=None,
    )

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    with (
        patch.object(batch_poller.settings, "OPENAI_INPUT_TOKEN_PRICE", 1.0),
        patch.object(batch_poller.settings, "OPENAI_OUTPUT_TOKEN_PRICE", 2.0),
        patch.object(batch_poller.settings, "OPENAI_BATCH_INPUT_TOKEN_PRICE", None),
        patch.object(batch_poller.settings, "OPENAI_BATCH_OUTPUT_TOKEN_PRICE", None),
    ):
        await batch_poller._process_batch(db_engine, client, job.openai_batch_id, [job], _LOG)

    review = await _fetch_ai_review(engine)
    assert review is not None
    assert review["_ai_review_cost"] == 100


# ---------------------------------------------------------------------------
# _process_batch — completed with per-request errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_batch_completed_with_error_file_clears_flag_and_notifies(engine: object) -> None:
    """Per-request errors in a 'completed' batch send AI_REVIEW_FAILED and clear submit_to_ai."""
    await _seed_submission(engine)
    await _insert_batch_job(engine)

    output_text = ""  # no successful lines
    error_text = _make_error_jsonl(message="Model overloaded")
    batch = _make_batch_obj(
        "completed",
        output_file_id=None,
        error_file_id="file-err-001",
        request_counts_completed=0,
        request_counts_failed=1,
    )
    batch.output_file_id = None
    client = _make_mock_client(batch, output_text=output_text, error_text=error_text)

    job = BatchJobRow(
        id="job-id",
        submission_id=_SUBMISSION_ID,
        openai_batch_id=_OPENAI_BATCH_ID,
        local_status=ArenaAIBatchJobStatus.SUBMITTED.value,
        input_file_id="file-input-001",
        code_file_id="file-code-001",
        statement_file_id="file-stmt-001",
        error_file_id=None,
    )

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    with (
        patch.object(batch_poller.settings, "OPENAI_INPUT_TOKEN_PRICE", 1.0),
        patch.object(batch_poller.settings, "OPENAI_OUTPUT_TOKEN_PRICE", 2.0),
        patch.object(batch_poller.settings, "OPENAI_BATCH_INPUT_TOKEN_PRICE", None),
        patch.object(batch_poller.settings, "OPENAI_BATCH_OUTPUT_TOKEN_PRICE", None),
    ):
        await batch_poller._process_batch(db_engine, client, job.openai_batch_id, [job], _LOG)

    assert await _fetch_ai_review(engine) is None
    assert await _fetch_submit_to_ai_flag(engine) is False
    notif = await _fetch_notification(engine, kind=ArenaNotificationKind.AI_REVIEW_FAILED.value)
    assert notif is not None

    # error_file_id stored on the batch row
    row = await _fetch_batch_job(engine)
    assert row is not None
    assert row["error_file_id"] == "file-err-001"


# ---------------------------------------------------------------------------
# _process_batch — failed / expired / cancelled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("openai_status", ["failed", "expired", "cancelled", "cancelling"])
async def test_process_batch_terminal_failure_clears_flag_and_notifies(engine: object, openai_status: str) -> None:
    """Terminal failure statuses send AI_REVIEW_FAILED notification and clear submit_to_ai."""
    # Use unique IDs per status to avoid PK collisions across parametrize runs
    uid = f"p-{openai_status[:3]}"
    sub_id = f"sub-{uid}"
    prob_id = f"prob-{uid}"
    user_id = f"user-{uid}"
    ob_id = f"batch-{uid}"

    await _seed_submission(engine, submission_id=sub_id, problem_id=prob_id, user_id=user_id)
    await _insert_batch_job(engine, submission_id=sub_id, openai_batch_id=ob_id)

    batch = _make_batch_obj(openai_status)
    client = _make_mock_client(batch)

    job = BatchJobRow(
        id="job-id",
        submission_id=sub_id,
        openai_batch_id=ob_id,
        local_status=ArenaAIBatchJobStatus.SUBMITTED.value,
        input_file_id="file-input-001",
        code_file_id="file-code-001",
        statement_file_id="file-stmt-001",
        error_file_id=None,
    )

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]

    await batch_poller._process_batch(db_engine, client, job.openai_batch_id, [job], _LOG)

    # submit_to_ai cleared so user can retry
    assert await _fetch_submit_to_ai_flag(engine, sub_id) is False
    # failure notification sent
    notif = await _fetch_notification(engine, sub_id, kind=ArenaNotificationKind.AI_REVIEW_FAILED.value)
    assert notif is not None
    # no review row
    assert await _fetch_ai_review(engine, sub_id) is None


@pytest.mark.asyncio
async def test_process_batch_failed_finalizes_local_status(engine: object) -> None:
    """Failed batch sets local_status='failed' and records completed_at."""
    await _seed_submission(engine)
    await _insert_batch_job(engine)

    batch = _make_batch_obj("failed")
    client = _make_mock_client(batch)

    job = BatchJobRow(
        id="job-id",
        submission_id=_SUBMISSION_ID,
        openai_batch_id=_OPENAI_BATCH_ID,
        local_status=ArenaAIBatchJobStatus.SUBMITTED.value,
        input_file_id="file-input-001",
        code_file_id="file-code-001",
        statement_file_id="file-stmt-001",
        error_file_id=None,
    )

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    await batch_poller._process_batch(db_engine, client, job.openai_batch_id, [job], _LOG)

    row = await _fetch_batch_job(engine)
    assert row is not None
    assert row["local_status"] == ArenaAIBatchJobStatus.FAILED.value
    assert row["completed_at"] is not None


@pytest.mark.asyncio
async def test_process_batch_failed_deletes_openai_files(engine: object) -> None:
    """All uploaded OpenAI files are deleted even when the batch fails."""
    await _seed_submission(engine)
    await _insert_batch_job(engine)

    batch = _make_batch_obj("failed")
    client = _make_mock_client(batch)

    job = BatchJobRow(
        id="job-id",
        submission_id=_SUBMISSION_ID,
        openai_batch_id=_OPENAI_BATCH_ID,
        local_status=ArenaAIBatchJobStatus.SUBMITTED.value,
        input_file_id="file-input-001",
        code_file_id="file-code-001",
        statement_file_id="file-stmt-001",
        error_file_id=None,
    )

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    await batch_poller._process_batch(db_engine, client, job.openai_batch_id, [job], _LOG)

    deleted_ids = {call.args[0] for call in client.files.delete.await_args_list}
    assert "file-input-001" in deleted_ids
    assert "file-code-001" in deleted_ids
    assert "file-stmt-001" in deleted_ids


@pytest.mark.asyncio
async def test_process_batch_file_delete_error_is_suppressed(engine: object) -> None:
    """A failure to delete one OpenAI file does not abort cleanup of the others."""
    await _seed_submission(engine)
    await _insert_batch_job(engine)

    batch = _make_batch_obj("failed")
    client = _make_mock_client(batch)
    client.files.delete = AsyncMock(side_effect=Exception("Network timeout"))

    job = BatchJobRow(
        id="job-id",
        submission_id=_SUBMISSION_ID,
        openai_batch_id=_OPENAI_BATCH_ID,
        local_status=ArenaAIBatchJobStatus.SUBMITTED.value,
        input_file_id="file-input-001",
        code_file_id="file-code-001",
        statement_file_id="file-stmt-001",
        error_file_id=None,
    )

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    # Should not raise despite delete errors
    await batch_poller._process_batch(db_engine, client, job.openai_batch_id, [job], _LOG)

    # Batch row still finalized correctly
    row = await _fetch_batch_job(engine)
    assert row is not None
    assert row["local_status"] == ArenaAIBatchJobStatus.FAILED.value


# ---------------------------------------------------------------------------
# _poll_cycle — integration (no real OpenAI, mocked client)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_cycle_no_pending_jobs_skips_client_creation(engine: object) -> None:
    """When no pending jobs exist, the poll cycle returns without creating an OpenAI client."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    valkey_runtime = _make_valkey_runtime()

    with patch("aiassistant.batch_poller.AsyncOpenAI") as mock_ctor:
        await batch_poller._poll_cycle(db_engine, valkey_runtime, _LOG)

    mock_ctor.assert_not_called()
    valkey_runtime.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_cycle_no_pending_jobs_when_all_terminal(engine: object) -> None:
    """Terminal jobs are not picked up by the poll cycle."""
    await _seed_submission(engine)
    await _insert_batch_job(engine, local_status=ArenaAIBatchJobStatus.COMPLETED.value)

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    valkey_runtime = _make_valkey_runtime()

    with patch("aiassistant.batch_poller.AsyncOpenAI") as mock_ctor:
        await batch_poller._poll_cycle(db_engine, valkey_runtime, _LOG)

    mock_ctor.assert_not_called()
    valkey_runtime.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_cycle_processes_pending_job(engine: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pending job is retrieved and processed during the poll cycle."""
    await _seed_submission(engine)
    await _insert_batch_job(engine)

    output_text = _make_output_jsonl()
    batch = _make_batch_obj("completed", output_file_id="file-out-001")
    client = _make_mock_client(batch, output_text=output_text)

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    valkey_runtime = _make_valkey_runtime()
    monkeypatch.setattr(batch_poller.settings, "OPENAI_API_KEY", "sk-platform")
    monkeypatch.setattr(batch_poller.settings, "OPENAI_INPUT_TOKEN_PRICE", 1.0)
    monkeypatch.setattr(batch_poller.settings, "OPENAI_OUTPUT_TOKEN_PRICE", 2.0)
    monkeypatch.setattr(batch_poller.settings, "OPENAI_BATCH_INPUT_TOKEN_PRICE", None)
    monkeypatch.setattr(batch_poller.settings, "OPENAI_BATCH_OUTPUT_TOKEN_PRICE", None)

    with patch("aiassistant.batch_poller.AsyncOpenAI", return_value=client):
        await batch_poller._poll_cycle(db_engine, valkey_runtime, _LOG)

    review = await _fetch_ai_review(engine)
    assert review is not None
    row = await _fetch_batch_job(engine)
    assert row is not None
    assert row["local_status"] == ArenaAIBatchJobStatus.COMPLETED.value
    valkey_runtime.set.assert_awaited_once()
    key, raw_payload = valkey_runtime.set.await_args.args
    assert key == AI_BATCH_TURNAROUND_STATS_KEY
    assert json.loads(raw_payload)["sample_count"] == 1


@pytest.mark.asyncio
async def test_poll_cycle_skips_when_no_platform_api_key(engine: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """Poll cycle logs a warning and skips when no platform API key is configured."""
    await _seed_submission(engine)
    await _insert_batch_job(engine)

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    valkey_runtime = _make_valkey_runtime()
    monkeypatch.setattr(batch_poller.settings, "OPENAI_API_KEY", None)

    with patch("aiassistant.batch_poller.AsyncOpenAI") as mock_ctor:
        await batch_poller._poll_cycle(db_engine, valkey_runtime, _LOG)

    mock_ctor.assert_not_called()
    # Job still pending (not processed)
    async with db_engine.connect() as conn:
        pending = await get_pending_batch_jobs(conn)
    assert len(pending) == 1
    valkey_runtime.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_cycle_stats_failure_does_not_undo_completed_review(
    engine: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Valkey statistics failure does not undo completed batch processing."""
    await _seed_submission(engine)
    await _insert_batch_job(engine)

    output_text = _make_output_jsonl()
    batch = _make_batch_obj("completed", output_file_id="file-out-001")
    client = _make_mock_client(batch, output_text=output_text)
    valkey_runtime = _make_valkey_runtime()
    valkey_runtime.set.side_effect = RuntimeError("Valkey write failed")

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    monkeypatch.setattr(batch_poller.settings, "OPENAI_API_KEY", "sk-platform")
    monkeypatch.setattr(batch_poller.settings, "OPENAI_INPUT_TOKEN_PRICE", 1.0)
    monkeypatch.setattr(batch_poller.settings, "OPENAI_OUTPUT_TOKEN_PRICE", 2.0)
    monkeypatch.setattr(batch_poller.settings, "OPENAI_BATCH_INPUT_TOKEN_PRICE", None)
    monkeypatch.setattr(batch_poller.settings, "OPENAI_BATCH_OUTPUT_TOKEN_PRICE", None)

    with patch("aiassistant.batch_poller.AsyncOpenAI", return_value=client):
        await batch_poller._poll_cycle(db_engine, valkey_runtime, _LOG)

    assert await _fetch_ai_review(engine) is not None
    row = await _fetch_batch_job(engine)
    assert row is not None
    assert row["local_status"] == ArenaAIBatchJobStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_poll_cycle_refreshes_stats_once_for_multiple_completed_batches(
    engine: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One poll cycle publishes once after handling multiple completed batches."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    valkey_runtime = _make_valkey_runtime()
    jobs = [
        BatchJobRow(
            id=f"job-{index}",
            submission_id=f"submission-{index}",
            openai_batch_id=f"batch-{index}",
            local_status=ArenaAIBatchJobStatus.SUBMITTED.value,
            input_file_id=None,
            code_file_id=None,
            statement_file_id=None,
            error_file_id=None,
        )
        for index in range(2)
    ]
    monkeypatch.setattr(batch_poller.settings, "OPENAI_API_KEY", "sk-platform")

    with (
        patch("aiassistant.batch_poller._expire_stale_batches", AsyncMock()),
        patch("aiassistant.batch_poller.get_pending_batch_jobs", AsyncMock(return_value=jobs)),
        patch("aiassistant.batch_poller.AsyncOpenAI"),
        patch("aiassistant.batch_poller._process_batch", AsyncMock(return_value=True)) as process,
        patch(
            "aiassistant.batch_poller.refresh_batch_turnaround_stats",
            AsyncMock(),
        ) as refresh,
    ):
        await batch_poller._poll_cycle(db_engine, valkey_runtime, _LOG)

    assert process.await_count == 2
    refresh.assert_awaited_once_with(db_engine, valkey_runtime, _LOG)
