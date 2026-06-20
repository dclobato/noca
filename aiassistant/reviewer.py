#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""OpenAI Responses API integration for Arena code review.

Follows the reference implementation pattern: source code and problem statement
are uploaded as files (``purpose="user_data"``), referenced via ``input_file``
in the request body, and deleted from OpenAI storage after the response is
received. Cost is always computed from token usage when available; the
``used_platform_key`` flag on the result indicates whether the platform API key
or the user's own key was used.
"""

from __future__ import annotations

import contextlib
import pathlib
import tempfile
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from aiassistant.prompts import SYSTEM_PROMPT
from shared.language_registry import default_language_registry

_LANGUAGE_REGISTRY = default_language_registry()


@dataclass
class ReviewResult:
    """Result of a single AI code review call."""

    response_text: str
    input_tokens: int
    output_tokens: int
    total_cost: float | None
    used_platform_key: bool


async def call_ai_review(
    source_code: str,
    problem_statement: str,
    language_id: str,
    api_key: str,
    model: str,
    max_output_tokens: int,
    input_price: float,
    output_price: float,
    is_platform_key: bool,
    extra_task_instructions: str | None = None,
    image_base64: str | None = None,
    image_mime: str | None = None,
    image_caption: str | None = None,
) -> ReviewResult:
    """Upload code and statement to OpenAI and return the AI review result.

    Follows the reference implementation exactly: both files are uploaded as
    ``user_data`` purpose, referenced via ``input_file`` content type in the
    Responses API request, and deleted from OpenAI storage in a ``finally``
    block regardless of the API outcome.

    Args:
        source_code: The submitted source code to review.
        problem_statement: The Arena problem statement text (Markdown).
        language_id: Language key used to determine the upload file extension.
        api_key: OpenAI API key to use for this request.
        model: OpenAI model identifier (e.g. ``'gpt-5.4-mini'``).
        max_output_tokens: Maximum tokens allowed in the AI response.
        input_price: Price per 1 million input tokens in USD (for cost recording).
        output_price: Price per 1 million output tokens in USD (for cost recording).
        is_platform_key: When True, ``used_platform_key`` on the result is set to
            ``True``; cost is always computed regardless.
        extra_task_instructions: Optional extra instructions appended to the user
            task content.
        image_base64: Optional Base64-encoded problem image, sent as an inline data-URI
            ``input_image`` content block. Skipped when None or empty.
        image_mime: MIME type of the image (e.g. ``'image/png'``). Required when
            ``image_base64`` is provided; ignored otherwise.
        image_caption: Optional caption for the image, appended to the user prompt text.

    Returns:
        ReviewResult with response text, token counts, cost (when usage data is
            available), and the ``used_platform_key`` flag.

    Raises:
        openai.OpenAIError: On API-level errors after file uploads have been cleaned up.
    """
    client = AsyncOpenAI(api_key=api_key)
    lang = _LANGUAGE_REGISTRY.get(language_id)
    ext = lang.default_extension if lang is not None else ".txt"

    code_path: str | None = None
    stmt_path: str | None = None
    sub_file_id: str | None = None
    stmt_file_id: str | None = None

    try:
        try:
            with tempfile.NamedTemporaryFile(suffix=ext, mode="w", encoding="utf-8", delete=False) as cf:
                cf.write(source_code)
                code_path = cf.name
            with tempfile.NamedTemporaryFile(suffix=".md", mode="w", encoding="utf-8", delete=False) as sf:
                sf.write(problem_statement)
                stmt_path = sf.name

            with open(code_path, "rb") as f:
                sub_file = await client.files.create(file=f, purpose="user_data")
                sub_file_id = sub_file.id
            with open(stmt_path, "rb") as f:
                stmt_file = await client.files.create(file=f, purpose="user_data")
                stmt_file_id = stmt_file.id

            lang_info_lines: list[str] = []
            if lang is not None:
                lang_info_lines.append(f"Language: {lang.name}")
                if lang.version:
                    lang_info_lines.append(f"Version: {lang.version}")
                if lang.compile_cmd:
                    lang_info_lines.append(f"Compile command: {' '.join(lang.compile_cmd)}")
                lang_info_lines.append(f"Run command: {' '.join(lang.run_cmd)}")
            lang_context = ("\n\nLanguage context:\n" + "\n".join(lang_info_lines)) if lang_info_lines else ""

            user_text = (
                "Analyze the submitted program against the statement. "
                "Give hints only; do not provide the corrected solution." + lang_context
            )
            if extra_task_instructions:
                user_text += f"\n\n{extra_task_instructions}"
            if image_caption:
                user_text += f"\n\nImage caption: {image_caption}"

            image_content: list[Any] = []
            if image_base64 and image_mime:
                image_content.append(
                    {
                        "type": "input_image",
                        "image_url": f"data:{image_mime};base64,{image_base64}",
                    }
                )

            response = await client.responses.create(
                model=model,
                instructions=SYSTEM_PROMPT,
                max_output_tokens=max_output_tokens,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_file", "file_id": sub_file_id},
                            {"type": "input_file", "file_id": stmt_file_id},
                            *image_content,
                            {"type": "input_text", "text": user_text},
                        ],
                    }
                ],
            )
        finally:
            if sub_file_id is not None:
                with contextlib.suppress(Exception):
                    await client.files.delete(sub_file_id)
            if stmt_file_id is not None:
                with contextlib.suppress(Exception):
                    await client.files.delete(stmt_file_id)

    finally:
        if code_path is not None:
            pathlib.Path(code_path).unlink(missing_ok=True)
        if stmt_path is not None:
            pathlib.Path(stmt_path).unlink(missing_ok=True)

    usage = response.usage
    input_tokens = usage.input_tokens if usage is not None else 0
    output_tokens = usage.output_tokens if usage is not None else 0

    cost: float | None = None
    if usage is not None:
        cost = (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price

    return ReviewResult(
        response_text=response.output_text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_cost=cost,
        used_platform_key=is_platform_key,
    )
