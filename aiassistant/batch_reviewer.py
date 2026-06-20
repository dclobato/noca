#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""OpenAI Batch API integration for platform-funded Arena code reviews.

Provides two submission paths:

* ``submit_ai_batch_review`` — legacy single-item batch (kept for compatibility).
* ``submit_windowed_batch`` — multi-item windowed batch: accepts a list of
  ``StagedItem`` objects, uploads per-item files, builds one multi-line JSONL,
  and calls ``batches.create`` exactly once.

All uploaded OpenAI files are intentionally kept alive after batch creation.
The batch poller deletes them once a terminal status is reached.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from aiassistant.prompts import SYSTEM_PROMPT
from shared.language_registry import default_language_registry

_LANGUAGE_REGISTRY = default_language_registry()


@dataclass
class BatchSubmitResult:
    """Identifiers returned after successfully submitting an OpenAI batch.

    Attributes:
        openai_batch_id: OpenAI batch identifier, e.g. ``'batch_xxx'``.
        input_file_id: OpenAI file ID of the uploaded JSONL batch input.
        code_file_id: OpenAI file ID of the uploaded source-code file.
        statement_file_id: OpenAI file ID of the uploaded problem-statement file.
    """

    openai_batch_id: str
    input_file_id: str
    code_file_id: str
    statement_file_id: str


async def submit_ai_batch_review(
    *,
    source_code: str,
    problem_statement: str,
    language_id: str,
    submission_id: str,
    api_key: str,
    model: str,
    max_output_tokens: int,
    extra_task_instructions: str | None = None,
    image_base64: str | None = None,
    image_mime: str | None = None,
    image_caption: str | None = None,
) -> BatchSubmitResult:
    """Upload files and submit a single-item OpenAI batch for AI code review.

    Uploads the source code and problem statement as ``user_data`` files, builds
    a single-line JSONL batch payload using the Responses API format, uploads it
    as a ``batch`` file, and calls ``batches.create``.

    All three uploaded files are kept alive after this call — the caller (batch
    poller) is responsible for deleting them on terminal batch status.

    Args:
        source_code: The submitted source code to review.
        problem_statement: The Arena problem statement text (Markdown).
        language_id: Language key used to determine the upload file extension.
        submission_id: UUID of the ``arena_submissions`` row; used as ``custom_id``
            in the JSONL so the poller can match responses to submissions.
        api_key: OpenAI API key to use for this request (platform key).
        model: OpenAI model identifier, e.g. ``'gpt-5.4-mini'``.
        max_output_tokens: Maximum tokens allowed in the AI response.
        extra_task_instructions: Optional extra instructions appended to the user
            task content.
        image_base64: Optional Base64-encoded problem image sent as an inline
            data-URI ``input_image`` content block. Skipped when ``None`` or empty.
        image_mime: MIME type of the image, e.g. ``'image/png'``. Required when
            ``image_base64`` is provided; ignored otherwise.
        image_caption: Optional caption for the image, appended to the user prompt.

    Returns:
        BatchSubmitResult containing the OpenAI batch ID and all three file IDs.

    Raises:
        openai.OpenAIError: On any API-level error during file upload or batch creation.
    """
    client = AsyncOpenAI(api_key=api_key)
    lang = _LANGUAGE_REGISTRY.get(language_id)
    ext = lang.default_extension if lang is not None else ".txt"

    code_path: str | None = None
    stmt_path: str | None = None
    jsonl_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(suffix=ext, mode="w", encoding="utf-8", delete=False) as cf:
            cf.write(source_code)
            code_path = cf.name
        with tempfile.NamedTemporaryFile(suffix=".md", mode="w", encoding="utf-8", delete=False) as sf:
            sf.write(problem_statement)
            stmt_path = sf.name

        with open(code_path, "rb") as f:
            code_file = await client.files.create(file=f, purpose="user_data")
        with open(stmt_path, "rb") as f:
            stmt_file = await client.files.create(file=f, purpose="user_data")

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

        batch_line = {
            "custom_id": submission_id,
            "method": "POST",
            "url": "/v1/responses",
            "body": {
                "model": model,
                "instructions": SYSTEM_PROMPT,
                "max_output_tokens": max_output_tokens,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_file", "file_id": code_file.id},
                            {"type": "input_file", "file_id": stmt_file.id},
                            *image_content,
                            {"type": "input_text", "text": user_text},
                        ],
                    }
                ],
            },
        }

        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", encoding="utf-8", delete=False) as jf:
            jf.write(json.dumps(batch_line, ensure_ascii=False) + "\n")
            jsonl_path = jf.name

        with open(jsonl_path, "rb") as f:
            input_file = await client.files.create(file=f, purpose="batch")

        batch = await client.batches.create(
            input_file_id=input_file.id,
            endpoint="/v1/responses",
            completion_window="24h",
        )

        return BatchSubmitResult(
            openai_batch_id=batch.id,
            input_file_id=input_file.id,
            code_file_id=code_file.id,
            statement_file_id=stmt_file.id,
        )

    finally:
        for p in (code_path, stmt_path, jsonl_path):
            if p is not None:
                pathlib.Path(p).unlink(missing_ok=True)


@dataclass
class StagedItem:
    """One platform-key review job ready to be bundled into a windowed batch.

    Attributes:
        submission_id: UUID of the ``arena_submissions`` row (used as ``custom_id``).
        source_code: Submitted source code.
        problem_statement: Arena problem statement text (Markdown).
        language_id: Language key, e.g. ``'gcc-c17'``.
        extra_task_instructions: Optional extra prompt instructions.
        image_base64: Optional Base64-encoded problem image.
        image_mime: MIME type of the image, e.g. ``'image/png'``.
        image_caption: Optional image caption.
    """

    submission_id: str
    source_code: str
    problem_statement: str
    language_id: str
    extra_task_instructions: str | None
    image_base64: str | None
    image_mime: str | None
    image_caption: str | None


@dataclass
class WindowedBatchResult:
    """Result of submitting a windowed multi-item OpenAI batch.

    Attributes:
        openai_batch_id: The OpenAI batch identifier shared by all items.
        input_file_id: OpenAI JSONL batch input file ID (shared).
        per_item_file_ids: Mapping of ``submission_id`` →
            ``(code_file_id, statement_file_id)``.
    """

    openai_batch_id: str
    input_file_id: str
    per_item_file_ids: dict[str, tuple[str, str]]


async def submit_windowed_batch(
    items: list[StagedItem],
    api_key: str,
    model: str,
    max_output_tokens: int,
) -> WindowedBatchResult:
    """Upload per-item files, build one JSONL, and submit a single OpenAI batch.

    Each item becomes one JSONL line with ``custom_id = submission_id``. All
    uploaded files (code, statement per item; shared JSONL) are kept alive after
    this call — the batch poller deletes them on terminal status.

    Args:
        items: List of staged review jobs to bundle. Must be non-empty.
        api_key: Platform OpenAI API key.
        model: OpenAI model identifier, e.g. ``'gpt-5.4-mini'``.
        max_output_tokens: Maximum tokens allowed in each AI response.

    Returns:
        ``WindowedBatchResult`` with the shared batch/input file IDs and
        per-item code/statement file IDs.

    Raises:
        ValueError: When ``items`` is empty.
        openai.OpenAIError: On API-level errors.
    """
    if not items:
        raise ValueError("submit_windowed_batch requires at least one item")

    client = AsyncOpenAI(api_key=api_key)
    per_item_file_ids: dict[str, tuple[str, str]] = {}
    jsonl_lines: list[str] = []
    tmp_paths: list[str] = []
    jsonl_path: str | None = None

    try:
        for item in items:
            lang = _LANGUAGE_REGISTRY.get(item.language_id)
            ext = lang.default_extension if lang is not None else ".txt"

            with tempfile.NamedTemporaryFile(suffix=ext, mode="w", encoding="utf-8", delete=False) as code_tmp:
                code_tmp.write(item.source_code)
                code_tmp_name = code_tmp.name
            tmp_paths.append(code_tmp_name)

            with tempfile.NamedTemporaryFile(suffix=".md", mode="w", encoding="utf-8", delete=False) as stmt_tmp:
                stmt_tmp.write(item.problem_statement)
                stmt_tmp_name = stmt_tmp.name
            tmp_paths.append(stmt_tmp_name)

            with open(code_tmp_name, "rb") as f:
                code_file = await client.files.create(file=f, purpose="user_data")
            with open(stmt_tmp_name, "rb") as f:
                stmt_file = await client.files.create(file=f, purpose="user_data")

            per_item_file_ids[item.submission_id] = (code_file.id, stmt_file.id)

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
            if item.extra_task_instructions:
                user_text += f"\n\n{item.extra_task_instructions}"
            if item.image_caption:
                user_text += f"\n\nImage caption: {item.image_caption}"

            image_content: list[Any] = []
            if item.image_base64 and item.image_mime:
                image_content.append(
                    {
                        "type": "input_image",
                        "image_url": f"data:{item.image_mime};base64,{item.image_base64}",
                    }
                )

            batch_line = {
                "custom_id": item.submission_id,
                "method": "POST",
                "url": "/v1/responses",
                "body": {
                    "model": model,
                    "instructions": SYSTEM_PROMPT,
                    "max_output_tokens": max_output_tokens,
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_file", "file_id": code_file.id},
                                {"type": "input_file", "file_id": stmt_file.id},
                                *image_content,
                                {"type": "input_text", "text": user_text},
                            ],
                        }
                    ],
                },
            }
            jsonl_lines.append(json.dumps(batch_line, ensure_ascii=False))

        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", encoding="utf-8", delete=False) as jsonl_tmp:
            jsonl_tmp.write("\n".join(jsonl_lines) + "\n")
            jsonl_path = jsonl_tmp.name

        with open(jsonl_path, "rb") as f:
            input_file = await client.files.create(file=f, purpose="batch")

        batch = await client.batches.create(
            input_file_id=input_file.id,
            endpoint="/v1/responses",
            completion_window="24h",
        )

        return WindowedBatchResult(
            openai_batch_id=batch.id,
            input_file_id=input_file.id,
            per_item_file_ids=per_item_file_ids,
        )

    finally:
        for p in tmp_paths:
            pathlib.Path(p).unlink(missing_ok=True)
        if jsonl_path is not None:
            pathlib.Path(jsonl_path).unlink(missing_ok=True)
