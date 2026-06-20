#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for aiassistant.batch_reviewer.

All tests mock AsyncOpenAI so no real API calls are made.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiassistant.batch_reviewer import BatchSubmitResult, submit_ai_batch_review

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_file(file_id: str) -> MagicMock:
    """Return a mock OpenAI file object with the given id."""
    f = MagicMock()
    f.id = file_id
    return f


def _make_batch(batch_id: str) -> MagicMock:
    """Return a mock OpenAI batch object with the given id."""
    b = MagicMock()
    b.id = batch_id
    return b


def _make_mock_client(
    file_ids: list[str] | None = None,
    batch_id: str = "batch-test-001",
) -> MagicMock:
    """Build a mock AsyncOpenAI client for batch submission tests."""
    if file_ids is None:
        file_ids = ["file-code", "file-stmt", "file-jsonl"]

    mock = MagicMock()
    mock.files.create = AsyncMock(side_effect=[_make_file(fid) for fid in file_ids])
    mock.files.delete = AsyncMock(return_value=None)
    mock.batches.create = AsyncMock(return_value=_make_batch(batch_id))
    return mock


_COMMON_KWARGS: dict[str, object] = {
    "source_code": "for i in range(n): pass",
    "problem_statement": "# Problem\nFind the answer.",
    "language_id": "python3",
    "submission_id": "sub-uuid-1234",
    "api_key": "sk-test-key",
    "model": "gpt-5.4-mini",
    "max_output_tokens": 500,
}


# ---------------------------------------------------------------------------
# BatchSubmitResult dataclass
# ---------------------------------------------------------------------------


def test_batch_submit_result_fields() -> None:
    """BatchSubmitResult stores the four expected identifiers."""
    r = BatchSubmitResult(
        openai_batch_id="batch-abc",
        input_file_id="file-in",
        code_file_id="file-code",
        statement_file_id="file-stmt",
    )
    assert r.openai_batch_id == "batch-abc"
    assert r.input_file_id == "file-in"
    assert r.code_file_id == "file-code"
    assert r.statement_file_id == "file-stmt"


# ---------------------------------------------------------------------------
# submit_ai_batch_review — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_batch_review_returns_result() -> None:
    """Happy path: returns a BatchSubmitResult with the expected IDs."""
    mock = _make_mock_client(["file-code", "file-stmt", "file-jsonl"], batch_id="batch-ok")

    with patch("aiassistant.batch_reviewer.AsyncOpenAI", return_value=mock):
        result = await submit_ai_batch_review(**_COMMON_KWARGS)  # type: ignore[arg-type]

    assert isinstance(result, BatchSubmitResult)
    assert result.openai_batch_id == "batch-ok"
    assert result.code_file_id == "file-code"
    assert result.statement_file_id == "file-stmt"
    assert result.input_file_id == "file-jsonl"


@pytest.mark.asyncio
async def test_submit_batch_review_uploads_three_files() -> None:
    """files.create is called exactly three times: code, statement, JSONL."""
    mock = _make_mock_client()

    with patch("aiassistant.batch_reviewer.AsyncOpenAI", return_value=mock):
        await submit_ai_batch_review(**_COMMON_KWARGS)  # type: ignore[arg-type]

    assert mock.files.create.call_count == 3


@pytest.mark.asyncio
async def test_submit_batch_review_calls_batches_create() -> None:
    """batches.create is called once with the JSONL file id and /v1/responses endpoint."""
    mock = _make_mock_client()

    with patch("aiassistant.batch_reviewer.AsyncOpenAI", return_value=mock):
        await submit_ai_batch_review(**_COMMON_KWARGS)  # type: ignore[arg-type]

    mock.batches.create.assert_awaited_once()
    kwargs = mock.batches.create.call_args.kwargs
    assert kwargs["endpoint"] == "/v1/responses"
    assert kwargs["completion_window"] == "24h"
    assert kwargs["input_file_id"] == "file-jsonl"


@pytest.mark.asyncio
async def test_submit_batch_review_jsonl_has_correct_custom_id() -> None:
    """The JSONL batch line uses the submission_id as custom_id."""
    mock = _make_mock_client()
    captured_content: list[bytes] = []

    async def capturing_create(file: object, purpose: str) -> MagicMock:
        import io

        if isinstance(file, io.RawIOBase | io.BufferedIOBase):
            captured_content.append(file.read())
            file.seek(0)
        fid = ["file-code", "file-stmt", "file-jsonl"][mock.files.create.call_count - 1]
        return _make_file(fid)

    mock.files.create = AsyncMock(side_effect=capturing_create)

    with patch("aiassistant.batch_reviewer.AsyncOpenAI", return_value=mock):
        await submit_ai_batch_review(**_COMMON_KWARGS)  # type: ignore[arg-type]

    # Third upload is the JSONL; parse it
    assert len(captured_content) >= 3
    jsonl_bytes = captured_content[2]
    record = json.loads(jsonl_bytes.decode("utf-8").strip())
    assert record["custom_id"] == "sub-uuid-1234"
    assert record["url"] == "/v1/responses"
    assert record["method"] == "POST"


@pytest.mark.asyncio
async def test_submit_batch_review_jsonl_contains_input_file_blocks() -> None:
    """The JSONL body input content includes two input_file blocks (code + statement)."""
    mock = _make_mock_client()
    jsonl_bytes: list[bytes] = []

    async def capturing_create(file: object, purpose: str) -> MagicMock:
        import io

        if isinstance(file, (io.RawIOBase, io.BufferedIOBase)):
            jsonl_bytes.append(file.read())
            file.seek(0)
        fid = ["file-code", "file-stmt", "file-jsonl"][mock.files.create.call_count - 1]
        return _make_file(fid)

    mock.files.create = AsyncMock(side_effect=capturing_create)

    with patch("aiassistant.batch_reviewer.AsyncOpenAI", return_value=mock):
        await submit_ai_batch_review(**_COMMON_KWARGS)  # type: ignore[arg-type]

    record = json.loads(jsonl_bytes[2].decode("utf-8").strip())
    content = record["body"]["input"][0]["content"]
    file_blocks = [c for c in content if c["type"] == "input_file"]
    assert len(file_blocks) == 2


@pytest.mark.asyncio
async def test_submit_batch_review_jsonl_appends_extra_task_instructions() -> None:
    """The JSONL task content includes extra user-task instructions."""
    mock = _make_mock_client()
    jsonl_bytes: list[bytes] = []

    async def capturing_create(file: object, purpose: str) -> MagicMock:
        import io

        if isinstance(file, (io.RawIOBase, io.BufferedIOBase)):
            jsonl_bytes.append(file.read())
            file.seek(0)
        fid = ["file-code", "file-stmt", "file-jsonl"][mock.files.create.call_count - 1]
        return _make_file(fid)

    mock.files.create = AsyncMock(side_effect=capturing_create)

    with patch("aiassistant.batch_reviewer.AsyncOpenAI", return_value=mock):
        await submit_ai_batch_review(
            **_COMMON_KWARGS,
            extra_task_instructions="Respond in Brazilian Portuguese.",
        )  # type: ignore[arg-type]

    record = json.loads(jsonl_bytes[2].decode("utf-8").strip())
    content = record["body"]["input"][0]["content"]
    assert "Respond in Brazilian Portuguese." in content[-1]["text"]


@pytest.mark.asyncio
async def test_submit_batch_review_no_files_deleted_on_success() -> None:
    """Files are NOT deleted on success — the poller cleans them up on terminal status."""
    mock = _make_mock_client()

    with patch("aiassistant.batch_reviewer.AsyncOpenAI", return_value=mock):
        await submit_ai_batch_review(**_COMMON_KWARGS)  # type: ignore[arg-type]

    mock.files.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# Image and caption handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_batch_review_with_image_includes_input_image_block() -> None:
    """An input_image block appears in the JSONL body when image_base64 and image_mime are given."""
    mock = _make_mock_client()
    jsonl_bytes: list[bytes] = []

    async def capturing_create(file: object, purpose: str) -> MagicMock:
        import io

        if isinstance(file, (io.RawIOBase, io.BufferedIOBase)):
            jsonl_bytes.append(file.read())
            file.seek(0)
        fid = ["file-code", "file-stmt", "file-jsonl"][mock.files.create.call_count - 1]
        return _make_file(fid)

    mock.files.create = AsyncMock(side_effect=capturing_create)

    with patch("aiassistant.batch_reviewer.AsyncOpenAI", return_value=mock):
        await submit_ai_batch_review(
            **_COMMON_KWARGS,  # type: ignore[arg-type]
            image_base64="aGVsbG8=",
            image_mime="image/png",
        )

    record = json.loads(jsonl_bytes[2].decode("utf-8").strip())
    content = record["body"]["input"][0]["content"]
    image_blocks = [c for c in content if c["type"] == "input_image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["image_url"] == "data:image/png;base64,aGVsbG8="


@pytest.mark.asyncio
async def test_submit_batch_review_without_image_has_no_image_block() -> None:
    """No input_image block is added when image_base64 is not provided."""
    mock = _make_mock_client()
    jsonl_bytes: list[bytes] = []

    async def capturing_create(file: object, purpose: str) -> MagicMock:
        import io

        if isinstance(file, (io.RawIOBase, io.BufferedIOBase)):
            jsonl_bytes.append(file.read())
            file.seek(0)
        fid = ["file-code", "file-stmt", "file-jsonl"][mock.files.create.call_count - 1]
        return _make_file(fid)

    mock.files.create = AsyncMock(side_effect=capturing_create)

    with patch("aiassistant.batch_reviewer.AsyncOpenAI", return_value=mock):
        await submit_ai_batch_review(**_COMMON_KWARGS)  # type: ignore[arg-type]

    record = json.loads(jsonl_bytes[2].decode("utf-8").strip())
    content = record["body"]["input"][0]["content"]
    image_blocks = [c for c in content if c["type"] == "input_image"]
    assert image_blocks == []


@pytest.mark.asyncio
async def test_submit_batch_review_content_order_with_image() -> None:
    """Content block order: input_file, input_file, input_image, input_text."""
    mock = _make_mock_client()
    jsonl_bytes: list[bytes] = []

    async def capturing_create(file: object, purpose: str) -> MagicMock:
        import io

        if isinstance(file, (io.RawIOBase, io.BufferedIOBase)):
            jsonl_bytes.append(file.read())
            file.seek(0)
        fid = ["file-code", "file-stmt", "file-jsonl"][mock.files.create.call_count - 1]
        return _make_file(fid)

    mock.files.create = AsyncMock(side_effect=capturing_create)

    with patch("aiassistant.batch_reviewer.AsyncOpenAI", return_value=mock):
        await submit_ai_batch_review(
            **_COMMON_KWARGS,  # type: ignore[arg-type]
            image_base64="aGVsbG8=",
            image_mime="image/webp",
            image_caption="A grid.",
        )

    record = json.loads(jsonl_bytes[2].decode("utf-8").strip())
    types = [c["type"] for c in record["body"]["input"][0]["content"]]
    assert types == ["input_file", "input_file", "input_image", "input_text"]
