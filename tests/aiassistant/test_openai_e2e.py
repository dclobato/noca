#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""End-to-end OpenAI API smoke tests for AI assistant review paths.

These tests make real OpenAI API calls when ``NOCA_AI_OPENAI_API_KEY`` is available
from the environment or repository ``.env``. They are skipped otherwise.
"""

from __future__ import annotations

import contextlib
import os
from asyncio import wait_for
from unittest.mock import patch

import pytest
from dotenv import load_dotenv
from openai import AsyncOpenAI

from aiassistant.batch_reviewer import BatchSubmitResult, submit_ai_batch_review
from aiassistant.reviewer import ReviewResult, call_ai_review

load_dotenv(override=False)

_API_KEY = os.getenv("NOCA_AI_OPENAI_API_KEY")
_MODEL = os.getenv("NOCA_AI_OPENAI_MODEL", "gpt-5.4-mini")
_OPENAI_TIMEOUT_SECONDS = 20.0

pytestmark = pytest.mark.real_openai


def _require_openai_key() -> str:
    """Return the OpenAI API key or skip when it is not configured."""
    if not _API_KEY:
        pytest.skip("Set NOCA_AI_OPENAI_API_KEY in the environment or .env to run real OpenAI e2e tests")
    return _API_KEY


def _real_openai_client(api_key: str) -> AsyncOpenAI:
    """Build a real OpenAI client with short e2e-test timeouts."""
    return AsyncOpenAI(api_key=api_key, timeout=_OPENAI_TIMEOUT_SECONDS, max_retries=0)


@pytest.mark.asyncio
async def test_real_online_review_uses_openai_responses_api() -> None:
    """Online review path should make a real Responses API call and return text."""
    api_key = _require_openai_key()

    with patch("aiassistant.reviewer.AsyncOpenAI", side_effect=_real_openai_client):
        result = await wait_for(
            call_ai_review(
                source_code="print(1 + 1)\n",
                problem_statement="# Sum\nPrint the sum of 1 and 1.",
                language_id="python3",
                api_key=api_key,
                model=_MODEL,
                max_output_tokens=150,
                input_price=0.0,
                output_price=0.0,
                is_platform_key=False,
            ),
            timeout=30,
        )

    assert isinstance(result, ReviewResult)
    assert result.response_text.strip()
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.used_platform_key is False


@pytest.mark.asyncio
async def test_real_batch_review_submits_openai_batch_job() -> None:
    """Batch path should upload files and create a real OpenAI batch job."""
    api_key = _require_openai_key()
    result: BatchSubmitResult | None = None
    client = _real_openai_client(api_key)

    try:
        with patch("aiassistant.batch_reviewer.AsyncOpenAI", side_effect=_real_openai_client):
            result = await wait_for(
                submit_ai_batch_review(
                    source_code="print(1 + 1)\n",
                    problem_statement="# Sum\nPrint the sum of 1 and 1.",
                    language_id="python3",
                    submission_id="e2e-openai-batch-review",
                    api_key=api_key,
                    model=_MODEL,
                    max_output_tokens=150,
                ),
                timeout=30,
            )

        assert result.openai_batch_id.startswith("batch_")
        assert result.input_file_id.startswith("file-")
        assert result.code_file_id.startswith("file-")
        assert result.statement_file_id.startswith("file-")

        batch = await client.batches.retrieve(result.openai_batch_id)
        assert batch.id == result.openai_batch_id
        assert batch.endpoint == "/v1/responses"
        assert batch.input_file_id == result.input_file_id
    finally:
        if result is not None:
            with contextlib.suppress(Exception):
                await client.batches.cancel(result.openai_batch_id)
            for file_id in (
                result.input_file_id,
                result.code_file_id,
                result.statement_file_id,
            ):
                with contextlib.suppress(Exception):
                    await client.files.delete(file_id)
