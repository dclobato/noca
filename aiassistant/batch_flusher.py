#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Windowed batch flusher for platform-funded Arena AI reviews.

Wakes every ``5 × AI_BATCH_POLL_INTERVAL_SECONDS``, collects all
``arena_ai_batch_jobs`` rows in the ``staged`` state, uploads code and
problem-statement files for each, builds a single multi-line JSONL, and
submits one OpenAI batch for the entire window. This amortises the per-batch
overhead and aligns with the intended usage of the OpenAI Batch API.

Crash recovery: if the flusher dies after ``batches.create()`` but before the
DB commit, staged rows remain unchanged. The next window picks them up and
submits a fresh batch (the orphaned OpenAI batch from the crashed run is an
accepted side-effect, consistent with the risk profile of the prior single-item
implementation).
"""

from __future__ import annotations

import asyncio
import logging
from logging import Logger
from typing import TYPE_CHECKING

from aiassistant._loop_helpers import interruptible_sleep
from aiassistant.batch_reviewer import StagedItem, submit_windowed_batch
from aiassistant.config import settings
from aiassistant.db.batch_queries import get_staged_batch_jobs, mark_staged_jobs_submitted
from aiassistant.db.queries import get_problem_data, get_submission_for_review, get_user_prefered_language

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


def _prefered_language_instruction(preferred_language: str) -> str | None:
    """Return an extra instruction asking the AI to reply in the user's preferred language.

    Args:
        preferred_language: BCP-47 locale string, e.g. ``'pt-BR'``.

    Returns:
        Instruction string, or ``None`` for English (no override needed).
    """
    if not preferred_language or preferred_language.lower().startswith("en"):
        return None
    return f"Please write your review in the language identified by locale '{preferred_language}'."


async def run_batch_flusher_loop(
    engine: AsyncEngine,
    stop_event: asyncio.Event,
    logger: Logger,
    *,
    flush_trigger: asyncio.Event | None = None,
) -> None:
    """Flush staged platform-key review jobs into OpenAI batches periodically.

    Wakes every ``5 × AI_BATCH_POLL_INTERVAL_SECONDS``, or immediately when
    ``flush_trigger`` is set, collects all rows with ``local_status='staged'``,
    and submits them as one multi-item OpenAI batch. If no rows are staged the
    cycle is a no-op.

    Args:
        engine: Async SQLAlchemy engine for database access.
        stop_event: Event set by the main worker to request shutdown.
        logger: Logger instance from the calling worker.
        flush_trigger: Optional one-shot event that wakes the loop early.
    """
    window = 5 * settings.AI_BATCH_POLL_INTERVAL_SECONDS
    log = logger.getChild("batch_flusher")
    log.info("Batch flusher loop started (window=%.0fs)", window)

    while not stop_event.is_set():
        if await interruptible_sleep(stop_event, flush_trigger, window):
            break

        if stop_event.is_set():
            break

        try:
            await _flush_cycle(engine, log)
        except Exception:
            log.exception("Unhandled error in batch flusher cycle")

    log.info("Batch flusher loop stopped")


async def _flush_cycle(engine: AsyncEngine, log: logging.Logger) -> None:
    """Collect staged rows and submit them as one OpenAI batch.

    Args:
        engine: Async SQLAlchemy engine for database access.
        log: Logger instance.
    """
    async with engine.connect() as conn:
        staged = await get_staged_batch_jobs(conn)

    if not staged:
        return

    api_key = settings.OPENAI_API_KEY
    if api_key is None:
        log.error("No platform OpenAI API key configured — cannot flush %d staged job(s)", len(staged))
        return

    log.info("Batch flusher: collecting %d staged job(s) into one OpenAI batch", len(staged))

    items: list[StagedItem] = []
    async with engine.connect() as conn:
        for row in staged:
            sub = await get_submission_for_review(conn, row.submission_id)
            if sub is None:
                log.warning("Staged submission %s not found; skipping", row.submission_id)
                continue
            problem = await get_problem_data(conn, sub.problem_id)
            preferred_language = await get_user_prefered_language(conn, sub.user_id)
            items.append(
                StagedItem(
                    submission_id=row.submission_id,
                    source_code=sub.source_code,
                    problem_statement=problem.problem_statement or "",
                    language_id=sub.language_id,
                    extra_task_instructions=_prefered_language_instruction(preferred_language),
                    image_base64=problem.image_base64,
                    image_mime=problem.image_mime,
                    image_caption=problem.image_caption,
                )
            )

    if not items:
        log.warning("All staged rows were missing from DB — nothing to flush")
        return

    result = await submit_windowed_batch(
        items=items,
        api_key=api_key,
        model=settings.OPENAI_MODEL,
        max_output_tokens=settings.OPENAI_MAX_OUTPUT_TOKENS,
    )

    submission_ids = [it.submission_id for it in items]
    async with engine.begin() as conn:
        await mark_staged_jobs_submitted(
            conn,
            submission_ids=submission_ids,
            openai_batch_id=result.openai_batch_id,
            input_file_id=result.input_file_id,
            per_item_file_ids=result.per_item_file_ids,
        )

    log.info(
        "Batch flusher: submitted OpenAI batch %s for %d submission(s)",
        result.openai_batch_id,
        len(items),
    )

    # Validate that any staged rows without a DB submission were also deleted/skipped.
    skipped_ids = {r.submission_id for r in staged} - set(submission_ids)
    if skipped_ids:
        log.warning("Staged rows not flushed (submission missing from DB): %s", skipped_ids)
