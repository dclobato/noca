#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Integration tests for worker-to-OpenAI API communication.

These tests exercise ``worker._process_job`` through the real online and batch
reviewer functions while mocking only the OpenAI SDK client boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiassistant import worker
from tests.aiassistant.test_worker import _fetch_ai_review, _fetch_batch_job, _seed_review_submission


@dataclass
class _FakeUsage:
    """Minimal Responses API usage object."""

    input_tokens: int
    output_tokens: int


@dataclass
class _FakeResponse:
    """Minimal Responses API response object."""

    output_text: str
    usage: _FakeUsage


def _make_file(file_id: str) -> MagicMock:
    """Return a fake OpenAI file object."""
    fake_file = MagicMock()
    fake_file.id = file_id
    return fake_file


def _make_batch(batch_id: str) -> MagicMock:
    """Return a fake OpenAI batch object."""
    fake_batch = MagicMock()
    fake_batch.id = batch_id
    return fake_batch


def _capture_file_uploads(client: MagicMock, file_ids: list[str]) -> list[dict[str, object]]:
    """Patch ``files.create`` to capture upload purposes and bytes."""
    uploads: list[dict[str, object]] = []

    async def create_file(file: object, purpose: str) -> MagicMock:
        content = file.read()
        file.seek(0)
        uploads.append({"purpose": purpose, "content": content})
        return _make_file(file_ids[len(uploads) - 1])

    client.files.create = AsyncMock(side_effect=create_file)
    return uploads


@pytest.mark.asyncio
async def test_process_job_online_calls_responses_api_with_user_key(
    engine: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User-key jobs call the online Responses API and store the immediate result."""
    submission_id = await _seed_review_submission(
        engine,
        submission_id="submission-api-online",
        user_id="arena-user-api-online",
        problem_id="arena-problem-api-online",
    )
    monkeypatch.setattr(worker.settings, "OPENAI_API_KEY", "sk-platform")
    monkeypatch.setattr(worker.settings, "OPENAI_MODEL", "gpt-test-online")
    monkeypatch.setattr(worker.settings, "OPENAI_MAX_OUTPUT_TOKENS", 321)
    monkeypatch.setattr(worker.settings, "OPENAI_INPUT_TOKEN_PRICE", 1.0)
    monkeypatch.setattr(worker.settings, "OPENAI_OUTPUT_TOKEN_PRICE", 2.0)

    client = MagicMock()
    _capture_file_uploads(client, ["file-code-online", "file-stmt-online"])
    client.files.delete = AsyncMock(return_value=None)
    client.responses.create = AsyncMock(return_value=_FakeResponse("Hint from online path.", _FakeUsage(100, 50)))
    complete_job = AsyncMock()

    with (
        patch("aiassistant.reviewer.AsyncOpenAI", return_value=client) as openai_cls,
        patch.object(worker, "get_user_api_key", AsyncMock(return_value="sk-user")),
        patch.object(worker, "complete_arena_ai_review_job", complete_job),
    ):
        await worker._process_job(submission_id, engine, object())  # type: ignore[arg-type]

    openai_cls.assert_called_once_with(api_key="sk-user")
    client.responses.create.assert_awaited_once()
    call_kwargs = client.responses.create.await_args.kwargs
    assert call_kwargs["model"] == "gpt-test-online"
    assert call_kwargs["max_output_tokens"] == 321
    assert call_kwargs["instructions"] == worker.call_ai_review.__globals__["SYSTEM_PROMPT"]

    content = call_kwargs["input"][0]["content"]
    assert content[0] == {"type": "input_file", "file_id": "file-code-online"}
    assert content[1] == {"type": "input_file", "file_id": "file-stmt-online"}
    assert content[-1]["type"] == "input_text"

    review = await _fetch_ai_review(engine, submission_id)
    assert review is not None
    assert review["ai_response"] == "Hint from online path."
    assert review["_ai_review_cost"] == 200
    assert review["used_platform_key"] is False
    assert await _fetch_batch_job(engine, submission_id) is None
    complete_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_job_batch_stages_without_api_calls(
    engine: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Platform-key jobs are staged in DB without any OpenAI API calls.

    Actual file uploads and batch creation happen later in the flusher loop.
    """
    submission_id = await _seed_review_submission(
        engine,
        submission_id="submission-api-batch",
        user_id="arena-user-api-batch",
        problem_id="arena-problem-api-batch",
        problem_image_base64="aGVsbG8=",
        problem_image_mime="image/png",
        problem_image_caption="Example diagram.",
    )
    monkeypatch.setattr(worker.settings, "OPENAI_API_KEY", "sk-platform")
    monkeypatch.setattr(worker.settings, "OPENAI_MODEL", "gpt-test-batch")
    monkeypatch.setattr(worker.settings, "OPENAI_MAX_OUTPUT_TOKENS", 654)

    client = MagicMock()
    client.files.create = AsyncMock()
    client.batches.create = AsyncMock()
    complete_job = AsyncMock()

    with (
        patch("aiassistant.batch_reviewer.AsyncOpenAI", return_value=client) as openai_cls,
        patch.object(worker, "complete_arena_ai_review_job", complete_job),
    ):
        await worker._process_job(submission_id, engine, object())  # type: ignore[arg-type]

    # No OpenAI client instantiation — staging makes no API calls
    openai_cls.assert_not_called()
    client.files.create.assert_not_awaited()
    client.batches.create.assert_not_awaited()

    # Staged row inserted with null file/batch IDs
    batch_job = await _fetch_batch_job(engine, submission_id)
    assert batch_job is not None
    assert batch_job["local_status"] == "staged"
    assert batch_job["openai_batch_id"] is None
    assert batch_job["input_file_id"] is None
    assert batch_job["code_file_id"] is None
    assert batch_job["statement_file_id"] is None

    assert await _fetch_ai_review(engine, submission_id) is None
    complete_job.assert_awaited_once()
