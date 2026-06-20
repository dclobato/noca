#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Batch poller loop for platform-funded OpenAI batch AI reviews.

Polls ``arena_ai_batch_jobs`` rows that are not yet in a terminal state,
retrieves the corresponding OpenAI batch objects, and processes results or
failures as each batch reaches a terminal status.

This module owns the polling loop and per-batch orchestration. Status mapping
and response parsing live in ``aiassistant.batch_status``; terminal-result
handling (storing reviews, notifications, file cleanup) lives in
``aiassistant.batch_results``. No direct ORM model imports from ``arena``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from logging import Logger
from typing import TYPE_CHECKING

from openai import AsyncOpenAI

from aiassistant._loop_helpers import interruptible_sleep
from aiassistant.batch_results import (
    delete_openai_files,
    handle_completed_batch,
    handle_failed_batch,
    handle_stale_batch,
)
from aiassistant.batch_status import (
    extract_batch_error,
    is_terminal_status,
    openai_status_to_local,
)
from aiassistant.config import settings
from aiassistant.db.batch_queries import (
    BatchJobRow,
    finalize_batch_job,
    get_pending_batch_jobs,
    get_stale_batch_jobs,
    record_batch_cleanup_error,
    update_batch_job_poll,
)
from aiassistant.turnaround_stats import refresh_batch_turnaround_stats
from shared.enumerations import ArenaAIBatchJobStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from shared.services.valkey_service import ValkeyRuntime


async def run_batch_poller_loop(
    engine: AsyncEngine,
    valkey_runtime: ValkeyRuntime,
    stop_event: asyncio.Event,
    logger: Logger,
    *,
    poll_trigger: asyncio.Event | None = None,
) -> None:
    """Poll pending OpenAI batch jobs until ``stop_event`` is set.

    Wakes every ``AI_BATCH_POLL_INTERVAL_SECONDS``, or immediately when
    ``poll_trigger`` is set, queries all non-terminal ``arena_ai_batch_jobs``
    rows, and checks each batch via the OpenAI API. On terminal status the
    results are stored, notifications sent, and all uploaded OpenAI files
    deleted.

    Uses the platform API key (``settings.OPENAI_API_KEY``) exclusively — the
    batch path is only used for platform-funded reviews.

    Args:
        engine: Async SQLAlchemy engine for database access.
        valkey_runtime: Shared Valkey runtime for publishing turnaround statistics.
        stop_event: Event set by the main worker to request shutdown.
        logger: Logger instance from the calling worker.
        poll_trigger: Optional one-shot event that wakes the loop early.
    """
    log = logger.getChild("batch_poller")
    log.info("Batch poller loop started (interval=%.0fs)", settings.AI_BATCH_POLL_INTERVAL_SECONDS)

    while not stop_event.is_set():
        if await interruptible_sleep(stop_event, poll_trigger, settings.AI_BATCH_POLL_INTERVAL_SECONDS):
            break

        if stop_event.is_set():
            break

        try:
            await _poll_cycle(engine, valkey_runtime, log)
        except Exception:
            log.exception("Unhandled error in batch poller cycle")

    log.info("Batch poller loop stopped")


async def _poll_cycle(
    engine: AsyncEngine,
    valkey_runtime: ValkeyRuntime,
    log: logging.Logger,
) -> None:
    """Run one poll cycle: fetch pending jobs and process each unique OpenAI batch.

    Rows are grouped by ``openai_batch_id`` so a windowed batch (N submissions
    sharing one OpenAI batch) results in exactly one ``batches.retrieve()`` call,
    not N redundant calls.

    Args:
        engine: Async SQLAlchemy engine for database access.
        valkey_runtime: Shared Valkey runtime for publishing turnaround statistics.
        log: Logger instance.
    """
    await _expire_stale_batches(engine, log)

    async with engine.connect() as conn:
        pending = await get_pending_batch_jobs(conn)

    if not pending:
        return

    # Group by openai_batch_id — windowed batches share one OpenAI batch ID.
    groups: dict[str, list[BatchJobRow]] = {}
    for job in pending:
        if job.openai_batch_id is None:
            log.warning("Skipping row %s with null openai_batch_id (status=%s)", job.id, job.local_status)
            continue
        groups.setdefault(job.openai_batch_id, []).append(job)

    if not groups:
        return

    log.debug("Batch poller: %d unique OpenAI batch(es) to check (%d row(s) total)", len(groups), len(pending))

    api_key = settings.OPENAI_API_KEY
    if api_key is None:
        log.warning("No platform OpenAI API key configured — cannot poll batch jobs")
        return

    client = AsyncOpenAI(api_key=api_key)

    completed_batch_seen = False
    for openai_batch_id, jobs in groups.items():
        try:
            completed_batch_seen = (
                await _process_batch(engine, client, openai_batch_id, jobs, log) or completed_batch_seen
            )
        except Exception:
            log.exception("Failed to process OpenAI batch %s", openai_batch_id)

    if completed_batch_seen:
        try:
            await refresh_batch_turnaround_stats(engine, valkey_runtime, log)
        except Exception:
            log.exception("Failed to refresh AI batch turnaround statistics")


async def _process_batch(
    engine: AsyncEngine,
    client: AsyncOpenAI,
    openai_batch_id: str,
    jobs: list[BatchJobRow],
    log: logging.Logger,
) -> bool:
    """Retrieve one OpenAI batch, update all sibling rows, and handle terminal results.

    Multiple ``arena_ai_batch_jobs`` rows may share the same ``openai_batch_id``
    when they were bundled in a single windowed flush. This function retrieves
    the batch once and applies status updates to all rows via the shared ID.

    Args:
        engine: Async SQLAlchemy engine for database access.
        client: Authenticated AsyncOpenAI client (platform key).
        openai_batch_id: The OpenAI batch ID shared by all rows in ``jobs``.
        jobs: All ``BatchJobRow`` objects belonging to this OpenAI batch.
        log: Logger instance.

    Returns:
        ``True`` when a completed OpenAI batch was fully handled and turnaround
        statistics must be refreshed; otherwise ``False``.
    """
    representative = jobs[0]
    now = datetime.now(UTC)
    batch = await client.batches.retrieve(openai_batch_id)
    openai_status = batch.status or "unknown"
    terminal = is_terminal_status(openai_status)
    new_local = openai_status_to_local(openai_status) if terminal else ArenaAIBatchJobStatus.POLLING.value

    rc = batch.request_counts
    # update_batch_job_poll uses WHERE openai_batch_id = ? → updates all sibling rows.
    async with engine.begin() as conn:
        affected = await update_batch_job_poll(
            conn,
            openai_batch_id=openai_batch_id,
            openai_status=openai_status,
            last_polled_at=now,
            local_status=new_local,
            request_counts_total=rc.total if rc is not None else None,
            request_counts_completed=rc.completed if rc is not None else None,
            request_counts_failed=rc.failed if rc is not None else None,
        )

    # Every sibling was already expiring/terminal (e.g. the stale detector expired
    # this batch). Do not store results, notify, or finalize over an expired batch.
    if affected == 0:
        log.info(
            "Batch %s already finalized/expired by another path; skipping result handling",
            openai_batch_id,
        )
        return False

    if not terminal:
        log.debug("Batch %s still in progress (openai_status=%s)", openai_batch_id, openai_status)
        return False

    log.info(
        "Batch %s reached terminal status (openai_status=%s, local_status=%s, submissions=%d)",
        openai_batch_id,
        openai_status,
        new_local,
        len(jobs),
    )

    error_file_id: str | None = None
    submission_ids = [j.submission_id for j in jobs]
    if openai_status == "completed":
        error_file_id, last_error = await handle_completed_batch(engine, client, representative, batch, log)
    else:
        # failed / expired / cancelling→cancelled
        last_error = extract_batch_error(batch)
        await handle_failed_batch(engine, submission_ids, openai_batch_id, last_error, log)

    # finalize_batch_job uses WHERE openai_batch_id = ? → updates all sibling rows.
    async with engine.begin() as conn:
        await finalize_batch_job(
            conn,
            openai_batch_id=openai_batch_id,
            local_status=new_local,
            completed_at=datetime.now(UTC),
            error_file_id=error_file_id,
            last_error=last_error,
        )

    # Delete all OpenAI files: input_file_id is shared, code/stmt are per-row.
    # delete_openai_files suppresses individual errors — duplicates are safe.
    for job in jobs:
        await delete_openai_files(client, job, error_file_id, log)

    return openai_status == "completed"


async def _expire_stale_batches(engine: AsyncEngine, log: logging.Logger) -> None:
    """Find and locally expire batch jobs that never reached a terminal status.

    Runs at the top of every poll cycle. A batch is stale when its ``submitted_at``
    is older than ``AI_BATCH_STALE_HOURS``. Stale rows are grouped by their shared
    ``openai_batch_id``; each group is expired locally (credit refunded, user
    notified) and then, when a platform key is available, the OpenAI batch is
    cancelled and its uploaded files deleted.

    Args:
        engine: Async SQLAlchemy engine for database access.
        log: Logger instance.
    """
    threshold = datetime.now(UTC) - timedelta(hours=settings.AI_BATCH_STALE_HOURS)
    async with engine.connect() as conn:
        stale = await get_stale_batch_jobs(conn, threshold)
    if not stale:
        return

    groups: dict[str, list[BatchJobRow]] = {}
    for job in stale:
        if job.openai_batch_id is None:
            continue
        groups.setdefault(job.openai_batch_id, []).append(job)

    api_key = settings.OPENAI_API_KEY
    for openai_batch_id, jobs in groups.items():
        oldest = min((j.submitted_at for j in jobs if j.submitted_at is not None), default=None)
        log.warning(
            "Stale batch %s (%d submission(s), oldest submitted_at=%s): expiring locally",
            openai_batch_id,
            len(jobs),
            oldest,
        )
        claimed = await handle_stale_batch(engine, jobs, log)

        # Only cancel the shared OpenAI batch when *this* worker expired the whole
        # group. If some rows were already terminal (e.g. another worker completed
        # the batch), cancelling/deleting its files could destroy a legitimately
        # finished batch, so we leave it alone.
        if len(claimed) != len(jobs):
            log.info(
                "Batch %s only partially claimed (%d/%d); skipping OpenAI cancellation",
                openai_batch_id,
                len(claimed),
                len(jobs),
            )
            continue
        if api_key is None:
            log.warning(
                "No platform API key — cannot cancel batch %s or delete its files",
                openai_batch_id,
            )
            continue
        client = AsyncOpenAI(api_key=api_key)
        await _cancel_and_cleanup(engine, client, openai_batch_id, jobs, log)


async def _cancel_and_cleanup(
    engine: AsyncEngine,
    client: AsyncOpenAI,
    openai_batch_id: str,
    jobs: list[BatchJobRow],
    log: logging.Logger,
) -> None:
    """Best-effort cancel a stale OpenAI batch and delete its uploaded files.

    Cancellation and file-deletion failures do not raise (OpenAI auto-expires
    batches after 24 h and deletes their files, so abandoned resources are bounded
    in time) but are recorded durably in ``last_error`` so operators can act.

    Args:
        engine: Async SQLAlchemy engine for recording cleanup errors.
        client: Authenticated AsyncOpenAI client (platform key).
        openai_batch_id: The OpenAI batch ID shared by all rows in ``jobs``.
        jobs: All ``BatchJobRow`` objects belonging to this OpenAI batch.
        log: Logger instance.
    """
    cleanup_errors: list[str] = []

    try:
        await client.batches.cancel(openai_batch_id)
        log.info("Cancelled OpenAI batch %s", openai_batch_id)
    except Exception as exc:
        log.warning(
            "Failed to cancel stale OpenAI batch %s; OpenAI auto-expires it within 24 h",
            openai_batch_id,
        )
        cleanup_errors.append(f"cancel failed: {exc}")

    failed_files: list[str] = []
    for job in jobs:
        failed_files.extend(await delete_openai_files(client, job, job.error_file_id, log))
    if failed_files:
        cleanup_errors.append(f"file delete failed: {', '.join(failed_files)}")

    if cleanup_errors:
        message = f"stale-cleanup: {'; '.join(cleanup_errors)}"
        with contextlib.suppress(Exception):
            async with engine.begin() as conn:
                await record_batch_cleanup_error(conn, openai_batch_id, message)
