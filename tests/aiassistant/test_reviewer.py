#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for aiassistant.reviewer.

All tests mock AsyncOpenAI so no real API calls are made. The mocks
replicate the structure of the OpenAI Responses API response object.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiassistant.reviewer import (
    ReviewResult,
    call_ai_review,
)
from shared.language_registry import default_language_registry

# ---------------------------------------------------------------------------
# Fake OpenAI response helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass
class _FakeResponse:
    output_text: str
    usage: _FakeUsage


def _make_fake_response(
    output_text: str = "Hint: check your loop bounds.",
    input_tokens: int = 200,
    output_tokens: int = 80,
) -> _FakeResponse:
    return _FakeResponse(
        output_text=output_text,
        usage=_FakeUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
    )


def _make_mock_client(response: _FakeResponse | None = None) -> MagicMock:
    """Build a mock AsyncOpenAI client that returns the given response."""
    if response is None:
        response = _make_fake_response()

    mock_client = MagicMock()

    # files.create returns an object with an .id attribute
    created_file = MagicMock()
    created_file.id = "file-abc123"
    mock_client.files.create = AsyncMock(return_value=created_file)
    mock_client.files.delete = AsyncMock(return_value=None)

    mock_client.responses.create = AsyncMock(return_value=response)
    return mock_client


# ---------------------------------------------------------------------------
# default_extension (via language registry)
# ---------------------------------------------------------------------------


def test_source_extension_python() -> None:
    registry = default_language_registry()
    assert registry["python3"].default_extension == ".py"


def test_source_extension_gcc() -> None:
    registry = default_language_registry()
    assert registry["gcc-c17"].default_extension == ".c"


def test_source_extension_gpp() -> None:
    registry = default_language_registry()
    assert registry["gcc-cpp23"].default_extension == ".cpp"


def test_source_extension_java() -> None:
    registry = default_language_registry()
    assert registry["java"].default_extension == ".java"


def test_source_extension_unknown_falls_back_to_txt() -> None:
    registry = default_language_registry()
    lang = registry.get("brainfuck-2.0")
    ext = lang.default_extension if lang is not None else ".txt"
    assert ext == ".txt"


# ---------------------------------------------------------------------------
# ReviewResult dataclass
# ---------------------------------------------------------------------------


def test_review_result_dataclass_fields() -> None:
    """ReviewResult stores all expected fields."""
    r = ReviewResult(
        response_text="Use a hash map.",
        input_tokens=100,
        output_tokens=50,
        total_cost=0.000475,
        used_platform_key=True,
    )
    assert r.response_text == "Use a hash map."
    assert r.input_tokens == 100
    assert r.output_tokens == 50
    assert r.total_cost == pytest.approx(0.000475)
    assert r.used_platform_key is True


def test_review_result_none_cost() -> None:
    """total_cost can be None when the API returns no usage data."""
    r = ReviewResult(response_text="Hint.", input_tokens=10, output_tokens=5, total_cost=None, used_platform_key=False)
    assert r.total_cost is None
    assert r.used_platform_key is False


# ---------------------------------------------------------------------------
# call_ai_review — unit tests with mocked AsyncOpenAI
# ---------------------------------------------------------------------------


_COMMON_KWARGS: dict[str, object] = {
    "source_code": "for i in range(n): pass",
    "problem_statement": "# Problem\nFind the answer.",
    "language_id": "python3",
    "api_key": "sk-test-key",
    "model": "gpt-5.4-mini",
    "max_output_tokens": 500,
    "input_price": 0.75,
    "output_price": 4.50,
}


@pytest.mark.asyncio
async def test_cost_calculated_with_platform_key() -> None:
    """When is_platform_key=True, total_cost is the sum of input and output costs."""
    fake_response = _make_fake_response(input_tokens=1_000_000, output_tokens=1_000_000)
    mock_client = _make_mock_client(fake_response)

    with patch("aiassistant.reviewer.AsyncOpenAI", return_value=mock_client):
        result = await call_ai_review(**_COMMON_KWARGS, is_platform_key=True)  # type: ignore[arg-type]

    # 1M input at $0.75/1M + 1M output at $4.50/1M = $5.25
    assert result.total_cost == pytest.approx(5.25)
    assert result.used_platform_key is True


@pytest.mark.asyncio
async def test_cost_calculated_with_user_key() -> None:
    """When is_platform_key=False, total_cost is still computed and used_platform_key is False."""
    mock_client = _make_mock_client(_make_fake_response(input_tokens=500_000, output_tokens=500_000))

    with patch("aiassistant.reviewer.AsyncOpenAI", return_value=mock_client):
        result = await call_ai_review(**_COMMON_KWARGS, is_platform_key=False)  # type: ignore[arg-type]

    # 0.5M input at $0.75/1M + 0.5M output at $4.50/1M = $2.625
    assert result.total_cost == pytest.approx(2.625)
    assert result.used_platform_key is False


@pytest.mark.asyncio
async def test_uploaded_files_deleted_on_success() -> None:
    """files.delete() is called twice (code file + statement file) on success."""
    mock_client = _make_mock_client()

    with patch("aiassistant.reviewer.AsyncOpenAI", return_value=mock_client):
        await call_ai_review(**_COMMON_KWARGS, is_platform_key=True)  # type: ignore[arg-type]

    assert mock_client.files.delete.call_count == 2


@pytest.mark.asyncio
async def test_uploaded_files_deleted_on_api_error() -> None:
    """files.delete() is still called when responses.create() raises an exception."""
    mock_client = _make_mock_client()
    mock_client.responses.create = AsyncMock(side_effect=RuntimeError("API error"))

    with (
        patch("aiassistant.reviewer.AsyncOpenAI", return_value=mock_client),
        pytest.raises(RuntimeError, match="API error"),
    ):
        await call_ai_review(**_COMMON_KWARGS, is_platform_key=True)  # type: ignore[arg-type]

    assert mock_client.files.delete.call_count == 2


@pytest.mark.asyncio
async def test_uploaded_code_file_deleted_when_statement_upload_fails() -> None:
    """The first uploaded file is cleaned up if the second upload fails."""
    mock_client = _make_mock_client()
    first_file = MagicMock()
    first_file.id = "file-code"
    mock_client.files.create = AsyncMock(side_effect=[first_file, RuntimeError("upload failed")])

    with (
        patch("aiassistant.reviewer.AsyncOpenAI", return_value=mock_client),
        pytest.raises(RuntimeError, match="upload failed"),
    ):
        await call_ai_review(**_COMMON_KWARGS, is_platform_key=True)  # type: ignore[arg-type]

    mock_client.files.delete.assert_awaited_once_with("file-code")


@pytest.mark.asyncio
async def test_request_uses_input_file_content_type() -> None:
    """The Responses API request uses input_file content type for both uploads."""
    mock_client = _make_mock_client()

    with patch("aiassistant.reviewer.AsyncOpenAI", return_value=mock_client):
        await call_ai_review(**_COMMON_KWARGS, is_platform_key=True)  # type: ignore[arg-type]

    call_kwargs = mock_client.responses.create.call_args.kwargs
    content = call_kwargs["input"][0]["content"]
    content_types = [item["type"] for item in content]
    assert "input_file" in content_types


@pytest.mark.asyncio
async def test_request_includes_instruction_text() -> None:
    """The Responses API request includes an input_text instruction block."""
    mock_client = _make_mock_client()

    with patch("aiassistant.reviewer.AsyncOpenAI", return_value=mock_client):
        await call_ai_review(**_COMMON_KWARGS, is_platform_key=True)  # type: ignore[arg-type]

    call_kwargs = mock_client.responses.create.call_args.kwargs
    content = call_kwargs["input"][0]["content"]
    text_blocks = [item for item in content if item["type"] == "input_text"]
    assert len(text_blocks) == 1
    assert "hints only" in text_blocks[0]["text"]


@pytest.mark.asyncio
async def test_request_appends_extra_task_instructions() -> None:
    """Extra task instructions are appended to the user input text."""
    mock_client = _make_mock_client()

    with patch("aiassistant.reviewer.AsyncOpenAI", return_value=mock_client):
        await call_ai_review(
            **_COMMON_KWARGS,
            is_platform_key=True,
            extra_task_instructions="Respond in Brazilian Portuguese.",
        )  # type: ignore[arg-type]

    call_kwargs = mock_client.responses.create.call_args.kwargs
    content = call_kwargs["input"][0]["content"]
    text_blocks = [item for item in content if item["type"] == "input_text"]
    assert "Respond in Brazilian Portuguese." in text_blocks[0]["text"]


@pytest.mark.asyncio
async def test_reviewer_uses_correct_model() -> None:
    """The model passed to call_ai_review is forwarded to responses.create."""
    mock_client = _make_mock_client()

    with patch("aiassistant.reviewer.AsyncOpenAI", return_value=mock_client):
        await call_ai_review(**{**_COMMON_KWARGS, "model": "gpt-custom-model"}, is_platform_key=False)  # type: ignore[arg-type]

    call_kwargs = mock_client.responses.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-custom-model"


@pytest.mark.asyncio
async def test_result_response_text_matches_api() -> None:
    """response_text in the result matches the API response output_text."""
    fake_response = _make_fake_response(output_text="Consider a two-pointer approach.")
    mock_client = _make_mock_client(fake_response)

    with patch("aiassistant.reviewer.AsyncOpenAI", return_value=mock_client):
        result = await call_ai_review(**_COMMON_KWARGS, is_platform_key=False)  # type: ignore[arg-type]

    assert result.response_text == "Consider a two-pointer approach."


@pytest.mark.asyncio
async def test_token_counts_match_api_usage() -> None:
    """input_tokens and output_tokens in the result match API usage."""
    fake_response = _make_fake_response(input_tokens=123, output_tokens=45)
    mock_client = _make_mock_client(fake_response)

    with patch("aiassistant.reviewer.AsyncOpenAI", return_value=mock_client):
        result = await call_ai_review(**_COMMON_KWARGS, is_platform_key=True)  # type: ignore[arg-type]

    assert result.input_tokens == 123
    assert result.output_tokens == 45


# ---------------------------------------------------------------------------
# call_ai_review — image and caption handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_image_content_block_included_when_image_provided() -> None:
    """An input_image block with a data-URI appears when image_base64 and image_mime are given."""
    mock_client = _make_mock_client()

    with patch("aiassistant.reviewer.AsyncOpenAI", return_value=mock_client):
        await call_ai_review(
            **_COMMON_KWARGS,  # type: ignore[arg-type]
            is_platform_key=False,
            image_base64="aGVsbG8=",
            image_mime="image/png",
        )

    content = mock_client.responses.create.call_args.kwargs["input"][0]["content"]
    image_blocks = [item for item in content if item["type"] == "input_image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["image_url"] == "data:image/png;base64,aGVsbG8="


@pytest.mark.asyncio
async def test_image_content_block_omitted_when_no_image() -> None:
    """No input_image block is added when image_base64 is not provided."""
    mock_client = _make_mock_client()

    with patch("aiassistant.reviewer.AsyncOpenAI", return_value=mock_client):
        await call_ai_review(**_COMMON_KWARGS, is_platform_key=False)  # type: ignore[arg-type]

    content = mock_client.responses.create.call_args.kwargs["input"][0]["content"]
    image_blocks = [item for item in content if item["type"] == "input_image"]
    assert image_blocks == []


@pytest.mark.asyncio
async def test_caption_appended_to_user_text() -> None:
    """The image caption is appended to the input_text block when provided."""
    mock_client = _make_mock_client()

    with patch("aiassistant.reviewer.AsyncOpenAI", return_value=mock_client):
        await call_ai_review(
            **_COMMON_KWARGS,  # type: ignore[arg-type]
            is_platform_key=False,
            image_base64="aGVsbG8=",
            image_mime="image/jpeg",
            image_caption="Figure 1: directed graph with 5 nodes.",
        )

    content = mock_client.responses.create.call_args.kwargs["input"][0]["content"]
    text_blocks = [item for item in content if item["type"] == "input_text"]
    assert len(text_blocks) == 1
    assert "Image caption: Figure 1: directed graph with 5 nodes." in text_blocks[0]["text"]


@pytest.mark.asyncio
async def test_caption_omitted_from_user_text_when_none() -> None:
    """The input_text block does not contain caption text when image_caption is None."""
    mock_client = _make_mock_client()

    with patch("aiassistant.reviewer.AsyncOpenAI", return_value=mock_client):
        await call_ai_review(**_COMMON_KWARGS, is_platform_key=False)  # type: ignore[arg-type]

    content = mock_client.responses.create.call_args.kwargs["input"][0]["content"]
    text_blocks = [item for item in content if item["type"] == "input_text"]
    assert len(text_blocks) == 1
    assert "Image caption" not in text_blocks[0]["text"]


@pytest.mark.asyncio
async def test_only_two_files_deleted_even_with_image() -> None:
    """files.delete() is called exactly twice (code + statement) even when an image is provided.

    Images are sent as inline data-URIs and are never uploaded to OpenAI storage.
    """
    mock_client = _make_mock_client()

    with patch("aiassistant.reviewer.AsyncOpenAI", return_value=mock_client):
        await call_ai_review(
            **_COMMON_KWARGS,  # type: ignore[arg-type]
            is_platform_key=False,
            image_base64="aGVsbG8=",
            image_mime="image/webp",
            image_caption="A sample grid.",
        )

    assert mock_client.files.delete.call_count == 2


@pytest.mark.asyncio
async def test_image_block_ordering_in_content() -> None:
    """The content list order is: code file, statement file, image (optional), text."""
    mock_client = _make_mock_client()

    with patch("aiassistant.reviewer.AsyncOpenAI", return_value=mock_client):
        await call_ai_review(
            **_COMMON_KWARGS,  # type: ignore[arg-type]
            is_platform_key=False,
            image_base64="aGVsbG8=",
            image_mime="image/png",
            image_caption="Caption.",
        )

    content = mock_client.responses.create.call_args.kwargs["input"][0]["content"]
    types = [item["type"] for item in content]
    assert types == ["input_file", "input_file", "input_image", "input_text"]
