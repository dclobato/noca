#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Terminal-batch result handling for the OpenAI batch poller.

Given a batch that has reached a terminal OpenAI status, these helpers parse
the output/error files, store review results, send notifications, and clean up
uploaded OpenAI files. The orchestration (when to call them) lives in
``aiassistant.batch_poller``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from aiassistant.batch_status import extract_output_text
from aiassistant.config import settings
from aiassistant.db.batch_queries import claim_batch_job_for_expiry, finalize_expired_batch_job
from aiassistant.db.credit_queries import refund_ai_credit_for_submission
from aiassistant.db.queries import (
    clear_submit_to_ai_flag,
    get_submission_for_review,
    store_ai_review_completed_notification,
    store_ai_review_failed_notification,
    store_ai_review_result,
    store_ai_review_stale_notification,
)

if TYPE_CHECKING:
    from openai import AsyncOpenAI

    from aiassistant.db.batch_queries import BatchJobRow

# Avoid a hard runtime import of the engine type (only used for annotations)
if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


async def handle_completed_batch(
    engine: AsyncEngine,
    client: AsyncOpenAI,
    job: BatchJobRow,
    batch: object,
    log: logging.Logger,
) -> tuple[str | None, str | None]:
    """Process a completed OpenAI batch: store results and send notifications.

    Parses the output file line-by-line.  Each successful line stores the
    review result and creates an ``AI_REVIEW_COMPLETED`` notification. Per-line
    failures (``error_file_id``) store a failure notification and clear the
    ``submit_to_ai`` flag so the user can retry.

    Cost is taken from per-line ``response.body.usage`` when available,
    falling back to batch-level usage when absent (single-item batches).

    Args:
        engine: Async SQLAlchemy engine for database access.
        client: Authenticated AsyncOpenAI client.
        job: Batch job row.
        batch: OpenAI batch object (cast from ``Any``).
        log: Logger instance.

    Returns:
        Tuple of (error_file_id, last_error) — both ``None`` when all lines
        succeeded.
    """
    from openai.types.batch import Batch

    b: Batch = batch  # type: ignore[assignment]
    error_file_id: str | None = None
    last_error: str | None = None

    if b.output_file_id:
        output_text = (await client.files.content(b.output_file_id)).text
        for raw_line in output_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                await _store_output_line(engine, record, job, log)
            except Exception:
                log.exception("Failed to process output line for batch %s", job.openai_batch_id)

    if b.error_file_id:
        error_file_id = b.error_file_id
        last_error = await _process_error_file(engine, client, b.error_file_id, job, log)

    return error_file_id, last_error


async def _process_error_file(
    engine: AsyncEngine,
    client: AsyncOpenAI,
    error_file_id: str,
    job: BatchJobRow,
    log: logging.Logger,
) -> str | None:
    """Process per-request error lines from a batch error file.

    Args:
        engine: Async SQLAlchemy engine for database access.
        client: Authenticated AsyncOpenAI client.
        error_file_id: OpenAI error output file ID.
        job: Batch job row (used as fallback for ``submission_id``).
        log: Logger instance.

    Returns:
        The last error message seen, or ``None`` when no lines were processed.
    """
    last_error: str | None = None
    error_text = (await client.files.content(error_file_id)).text
    for raw_line in error_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            submission_id = record.get("custom_id", job.submission_id)
            err_info = record.get("error") or {}
            last_error = err_info.get("message", "Unknown per-request error")
            log.warning(
                "Per-request error for submission %s in batch %s: %s",
                submission_id,
                job.openai_batch_id,
                last_error,
            )
            await _fail_submission(engine, submission_id)
        except Exception:
            log.exception("Failed to process error line for batch %s", job.openai_batch_id)
    return last_error


async def _store_output_line(
    engine: AsyncEngine,
    record: dict,  # type: ignore[type-arg]
    job: BatchJobRow,
    log: logging.Logger,
) -> None:
    """Parse one JSONL output line and store the review result.

    Args:
        engine: Async SQLAlchemy engine for database access.
        record: Parsed JSON object from the batch output file.
        job: Batch job row (used as fallback for ``submission_id``).
        log: Logger instance.
    """
    submission_id: str = record.get("custom_id", job.submission_id)
    response_obj = record.get("response") or {}
    status_code = response_obj.get("status_code")

    if status_code != 200:
        error = response_obj.get("error") or record.get("error") or {}
        msg = error.get("message", f"HTTP {status_code}")
        log.warning(
            "Non-200 response for submission %s in batch %s: %s",
            submission_id,
            job.openai_batch_id,
            msg,
        )
        await _fail_submission(engine, submission_id)
        return

    body = response_obj.get("body") or {}
    output_text = extract_output_text(body)
    if not output_text:
        log.warning(
            "Could not extract output text for submission %s in batch %s",
            submission_id,
            job.openai_batch_id,
        )
        return

    input_tokens, output_tokens, cost_micros = _compute_cost(body)

    async with engine.begin() as conn:
        sub = await get_submission_for_review(conn, submission_id)
        if sub is None:
            log.warning("Submission %s not found in DB during batch processing", submission_id)
            return
        await store_ai_review_result(
            conn,
            submission_id=submission_id,
            response_text=output_text,
            response_at=datetime.now(UTC),
            cost_micros=cost_micros,
            used_platform_key=True,
        )
        await store_ai_review_completed_notification(conn, sub)

    cost = None if cost_micros is None else cost_micros / 1_000_000
    log.info(
        "Batch review stored for submission %s (tokens=%d, cost=%s)",
        submission_id,
        input_tokens + output_tokens,
        f"${cost:.6f}" if cost is not None else "n/a",
    )


def _compute_cost(body: dict) -> tuple[int, int, int | None]:  # type: ignore[type-arg]
    """Compute token counts and cost (in micro-dollars) from a response body.

    Args:
        body: Parsed ``response.body`` dict from the batch output JSONL.

    Returns:
        Tuple of (input_tokens, output_tokens, cost_micros). ``cost_micros`` is
        ``None`` when no usage information is present.
    """
    usage = body.get("usage") or {}
    input_tokens = int(usage.get("input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    if not usage:
        return input_tokens, output_tokens, None
    cost = (input_tokens / 1_000_000) * settings.effective_batch_input_price + (
        output_tokens / 1_000_000
    ) * settings.effective_batch_output_price
    return input_tokens, output_tokens, round(cost * 1_000_000)


async def handle_failed_batch(
    engine: AsyncEngine,
    submission_ids: list[str],
    openai_batch_id: str,
    last_error: str | None,
    log: logging.Logger,
) -> None:
    """Send failure notifications for all submissions in a failed/expired batch.

    Args:
        engine: Async SQLAlchemy engine for database access.
        submission_ids: All submission UUIDs that were bundled in the batch.
        openai_batch_id: OpenAI batch identifier (for logging).
        last_error: Error message to log (if any).
        log: Logger instance.
    """
    log.warning(
        "Batch %s failed (%d submission(s), error=%s)",
        openai_batch_id,
        len(submission_ids),
        last_error,
    )
    for sid in submission_ids:
        await _fail_submission(engine, sid)


async def _fail_submission(engine: AsyncEngine, submission_id: str) -> None:
    """Clear ``submit_to_ai`` and send a platform-key failure notification.

    No-op when the submission no longer exists.

    Args:
        engine: Async SQLAlchemy engine for database access.
        submission_id: Arena submission identifier.
    """
    async with engine.begin() as conn:
        sub = await get_submission_for_review(conn, submission_id)
        if sub is not None:
            await clear_submit_to_ai_flag(conn, submission_id)
            await store_ai_review_failed_notification(conn, sub, is_user_key=False)


async def handle_stale_batch(
    engine: AsyncEngine,
    jobs: list[BatchJobRow],
    log: logging.Logger,
) -> list[BatchJobRow]:
    """Locally expire stale batch jobs, refunding credit and notifying each user.

    Each submission is processed in its own ``engine.begin()`` transaction so the
    claim, flag-clear, refund, notification, and finalization either all commit or
    all roll back together. If the transaction rolls back (crash mid-step) the row
    reverts to its pre-claim status and is retried on the next cycle. Because the
    row is only ``expired`` after a successful commit, a credit can never be
    refunded without the row being finalized.

    Args:
        engine: Async SQLAlchemy engine for database access.
        jobs: All ``BatchJobRow`` objects sharing one stale ``openai_batch_id``.
        log: Logger instance.

    Returns:
        The subset of ``jobs`` this call actually claimed and expired. Jobs that
        were already terminal or claimed by another worker are excluded, so the
        caller can decide whether the *whole* group was expired before cancelling
        the shared OpenAI batch.
    """
    now = datetime.now(UTC)
    claimed_jobs: list[BatchJobRow] = []
    for job in jobs:
        sub = None
        async with engine.begin() as conn:
            claimed = await claim_batch_job_for_expiry(conn, job.submission_id)
            if not claimed:
                log.debug(
                    "Submission %s already terminal/claimed; skipping stale expiry",
                    job.submission_id,
                )
                continue
            sub = await get_submission_for_review(conn, job.submission_id)
            await clear_submit_to_ai_flag(conn, job.submission_id)
            if sub is not None:
                await refund_ai_credit_for_submission(conn, sub.user_id, job.submission_id)
                await store_ai_review_stale_notification(conn, sub)
            await finalize_expired_batch_job(conn, job.submission_id, now)
        claimed_jobs.append(job)
        log.info(
            "Stale submission %s expired; credit_refunded=%s",
            job.submission_id,
            sub is not None,
        )
    return claimed_jobs


async def delete_openai_files(
    client: AsyncOpenAI,
    job: BatchJobRow,
    error_file_id: str | None,
    log: logging.Logger,
) -> list[str]:
    """Delete all OpenAI files associated with a completed or failed batch.

    Errors are suppressed individually so a failure to delete one file does
    not prevent the others from being cleaned up.

    Args:
        client: Authenticated AsyncOpenAI client.
        job: Batch job row containing the file IDs.
        error_file_id: OpenAI error output file ID, or ``None``.
        log: Logger instance.

    Returns:
        The file IDs that could not be deleted (empty when all succeeded), so
        callers can record a durable cleanup error.
    """
    file_ids = [
        fid
        for fid in (
            job.input_file_id,
            job.code_file_id,
            job.statement_file_id,
            error_file_id,
        )
        if fid is not None
    ]
    failed: list[str] = []
    for fid in file_ids:
        try:
            await client.files.delete(fid)
            log.debug("Deleted OpenAI file %s for batch %s", fid, job.openai_batch_id)
        except Exception:
            log.warning("Failed to delete OpenAI file %s for batch %s", fid, job.openai_batch_id)
            failed.append(fid)
    return failed
