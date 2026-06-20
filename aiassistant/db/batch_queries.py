#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""SQLAlchemy Core queries for AI batch job management.

All queries operate directly on shared schema tables — no arena ORM models are
imported. This preserves the architectural boundary between modules.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from shared.db_schema import arena_ai_batch_jobs
from shared.enumerations import ARENA_AI_BATCH_JOB_TERMINAL_STATUSES, ArenaAIBatchJobStatus


@dataclass
class BatchJobRow:
    """Batch job fields used by the poller, flusher, and idempotency guard.

    Attributes:
        id: Local UUID of the ``arena_ai_batch_jobs`` row.
        submission_id: UUID of the related ``arena_submissions`` row.
        openai_batch_id: OpenAI batch identifier, e.g. ``'batch_xxx'``. ``None``
            while ``local_status='staged'`` (not yet submitted to OpenAI).
        local_status: Local state machine value (see ``ArenaAIBatchJobStatus``).
        input_file_id: OpenAI JSONL batch input file ID, or ``None``.
        code_file_id: OpenAI source-code file ID, or ``None``.
        statement_file_id: OpenAI problem-statement file ID, or ``None``.
        error_file_id: OpenAI error output file ID, or ``None``.
        submitted_at: Timestamp the row was submitted to OpenAI, or ``None`` while
            ``local_status='staged'``. Drives stale-batch detection.
    """

    id: str
    submission_id: str
    openai_batch_id: str | None
    local_status: str
    input_file_id: str | None
    code_file_id: str | None
    statement_file_id: str | None
    error_file_id: str | None
    submitted_at: datetime | None = None


async def insert_batch_job(
    conn: AsyncConnection,
    submission_id: str,
    openai_batch_id: str,
    input_file_id: str,
    code_file_id: str,
    statement_file_id: str,
) -> None:
    """Insert a new ``arena_ai_batch_jobs`` row with ``local_status='submitted'``.

    Args:
        conn: Active async database connection (within a transaction).
        submission_id: UUID of the related ``arena_submissions`` row.
        openai_batch_id: OpenAI batch identifier returned by ``batches.create``.
        input_file_id: OpenAI JSONL batch input file ID.
        code_file_id: OpenAI source-code file ID.
        statement_file_id: OpenAI problem-statement file ID.
    """
    stmt = pg_insert(arena_ai_batch_jobs).values(
        id=str(uuid.uuid4()),
        submission_id=submission_id,
        openai_batch_id=openai_batch_id,
        local_status=ArenaAIBatchJobStatus.SUBMITTED.value,
        input_file_id=input_file_id,
        code_file_id=code_file_id,
        statement_file_id=statement_file_id,
        submitted_at=datetime.now(UTC),
    )
    await conn.execute(stmt)


async def insert_staged_batch_job(
    conn: AsyncConnection,
    submission_id: str,
) -> None:
    """Insert a new ``arena_ai_batch_jobs`` row with ``local_status='staged'``.

    Called by the dequeue worker when a platform-key job is accepted for
    windowed accumulation. No OpenAI API calls have been made yet; file IDs
    and the batch identifier are all ``NULL`` until the flusher fires.

    Args:
        conn: Active async database connection (within a transaction).
        submission_id: UUID of the related ``arena_submissions`` row.
    """
    stmt = pg_insert(arena_ai_batch_jobs).values(
        id=str(uuid.uuid4()),
        submission_id=submission_id,
        local_status=ArenaAIBatchJobStatus.STAGED.value,
    )
    await conn.execute(stmt)


async def get_staged_batch_jobs(conn: AsyncConnection) -> list[BatchJobRow]:
    """Return all batch jobs waiting in the staging window (``local_status='staged'``).

    Called by the batch flusher to collect submissions ready to be bundled
    into one OpenAI batch.

    Args:
        conn: Active async database connection.

    Returns:
        List of ``BatchJobRow`` objects ordered by creation time ascending.
    """
    stmt = (
        sa.select(
            arena_ai_batch_jobs.c.id,
            arena_ai_batch_jobs.c.submission_id,
            arena_ai_batch_jobs.c.openai_batch_id,
            arena_ai_batch_jobs.c.local_status,
            arena_ai_batch_jobs.c.input_file_id,
            arena_ai_batch_jobs.c.code_file_id,
            arena_ai_batch_jobs.c.statement_file_id,
            arena_ai_batch_jobs.c.error_file_id,
            arena_ai_batch_jobs.c.submitted_at,
        )
        .where(arena_ai_batch_jobs.c.local_status == ArenaAIBatchJobStatus.STAGED.value)
        .order_by(arena_ai_batch_jobs.c.created_at.asc())
    )
    rows = (await conn.execute(stmt)).mappings().all()
    return [_row_to_dataclass(r) for r in rows]


async def mark_staged_jobs_submitted(
    conn: AsyncConnection,
    submission_ids: list[str],
    openai_batch_id: str,
    input_file_id: str,
    per_item_file_ids: dict[str, tuple[str, str]],
) -> None:
    """Transition staged rows to ``submitted`` after a windowed batch is created.

    Updates each row identified by ``submission_ids`` with the shared
    ``openai_batch_id`` and ``input_file_id``, and per-submission
    ``code_file_id`` / ``statement_file_id`` from ``per_item_file_ids``.

    Args:
        conn: Active async database connection (within a transaction).
        submission_ids: UUIDs of the submissions that were bundled.
        openai_batch_id: OpenAI batch identifier from ``batches.create``.
        input_file_id: OpenAI JSONL batch input file ID (shared by all items).
        per_item_file_ids: Mapping of ``submission_id`` →
            ``(code_file_id, statement_file_id)``.
    """
    now = datetime.now(UTC)
    for sid in submission_ids:
        code_fid, stmt_fid = per_item_file_ids[sid]
        stmt = (
            sa.update(arena_ai_batch_jobs)
            .where(arena_ai_batch_jobs.c.submission_id == sid)
            .where(arena_ai_batch_jobs.c.local_status == ArenaAIBatchJobStatus.STAGED.value)
            .values(
                local_status=ArenaAIBatchJobStatus.SUBMITTED.value,
                openai_batch_id=openai_batch_id,
                input_file_id=input_file_id,
                code_file_id=code_fid,
                statement_file_id=stmt_fid,
                submitted_at=now,
            )
        )
        await conn.execute(stmt)


async def get_batch_job_for_submission(
    conn: AsyncConnection,
    submission_id: str,
) -> BatchJobRow | None:
    """Return any existing batch job for a submission (active or terminal), or ``None``.

    Used by ``_process_job`` as an idempotency guard. A *non-terminal* row means
    a dispatch is still in flight and the job is skipped.  A *terminal* row is a
    spent attempt that ``_process_job`` deletes (via ``delete_batch_job``) so a
    re-request can submit a fresh batch.  Both cases must be detected here
    because the unique constraint ``arena_ai_batch_jobs_submission_id_key``
    allows only one row per submission — a leftover terminal row would otherwise
    make ``insert_batch_job`` raise ``IntegrityError``.

    Args:
        conn: Active async database connection.
        submission_id: UUID of the ``arena_submissions`` row.

    Returns:
        ``BatchJobRow`` when any row exists, ``None`` otherwise.
    """
    stmt = sa.select(
        arena_ai_batch_jobs.c.id,
        arena_ai_batch_jobs.c.submission_id,
        arena_ai_batch_jobs.c.openai_batch_id,
        arena_ai_batch_jobs.c.local_status,
        arena_ai_batch_jobs.c.input_file_id,
        arena_ai_batch_jobs.c.code_file_id,
        arena_ai_batch_jobs.c.statement_file_id,
        arena_ai_batch_jobs.c.error_file_id,
        arena_ai_batch_jobs.c.submitted_at,
    ).where(arena_ai_batch_jobs.c.submission_id == submission_id)
    row = (await conn.execute(stmt)).mappings().first()
    if row is None:
        return None
    return _row_to_dataclass(row)


async def delete_batch_job(conn: AsyncConnection, submission_id: str) -> None:
    """Delete the ``arena_ai_batch_jobs`` row for a submission, if present.

    Used by the idempotency guard to clear a *terminal* batch row (failed,
    expired, cancelled, or completed-without-a-stored-review) so a legitimate
    user re-request can submit a fresh batch.  The unique constraint on
    ``submission_id`` otherwise blocks ``insert_batch_job``.  OpenAI files were
    already deleted when the previous row reached its terminal state, so only
    the database row needs removing here.

    Args:
        conn: Active async database connection (within a transaction).
        submission_id: UUID of the ``arena_submissions`` row.
    """
    stmt = sa.delete(arena_ai_batch_jobs).where(arena_ai_batch_jobs.c.submission_id == submission_id)
    await conn.execute(stmt)


async def get_pending_batch_jobs(conn: AsyncConnection) -> list[BatchJobRow]:
    """Return all batch jobs that are pollable (submitted/polling, not staged or terminal).

    Excludes ``staged`` rows (no ``openai_batch_id`` yet) and all terminal statuses
    so the poller only retrieves rows that have a real OpenAI batch to check.

    Args:
        conn: Active async database connection.

    Returns:
        List of ``BatchJobRow`` objects ordered by creation time ascending.
    """
    excluded = ARENA_AI_BATCH_JOB_TERMINAL_STATUSES | {
        ArenaAIBatchJobStatus.STAGED.value,
        ArenaAIBatchJobStatus.EXPIRING.value,
    }
    stmt = (
        sa.select(
            arena_ai_batch_jobs.c.id,
            arena_ai_batch_jobs.c.submission_id,
            arena_ai_batch_jobs.c.openai_batch_id,
            arena_ai_batch_jobs.c.local_status,
            arena_ai_batch_jobs.c.input_file_id,
            arena_ai_batch_jobs.c.code_file_id,
            arena_ai_batch_jobs.c.statement_file_id,
            arena_ai_batch_jobs.c.error_file_id,
            arena_ai_batch_jobs.c.submitted_at,
        )
        .where(arena_ai_batch_jobs.c.local_status.not_in(excluded))
        .order_by(arena_ai_batch_jobs.c.created_at.asc())
    )
    rows = (await conn.execute(stmt)).mappings().all()
    return [_row_to_dataclass(r) for r in rows]


async def update_batch_job_poll(
    conn: AsyncConnection,
    openai_batch_id: str,
    openai_status: str,
    last_polled_at: datetime,
    local_status: str,
    request_counts_total: int | None = None,
    request_counts_completed: int | None = None,
    request_counts_failed: int | None = None,
) -> int:
    """Update poll-cycle fields on a batch job row.

    Called every time the poller retrieves a batch from OpenAI, regardless of
    whether the status has changed. Rows that are mid-expiry (``expiring``) or
    already terminal are guarded out, so a poll that races the stale detector
    cannot resurrect an expired batch.

    Args:
        conn: Active async database connection (within a transaction).
        openai_batch_id: OpenAI batch identifier to match.
        openai_status: Raw OpenAI status string from the batch object.
        last_polled_at: Timestamp of this poll cycle.
        local_status: Updated local status (e.g. transitioned to ``polling``).
        request_counts_total: Total request count from the batch object, or ``None``.
        request_counts_completed: Completed count from the batch object, or ``None``.
        request_counts_failed: Failed count from the batch object, or ``None``.

    Returns:
        Number of sibling rows updated. ``0`` means every row sharing this
        ``openai_batch_id`` was already expiring/terminal — the caller must not
        proceed to store results or finalize the batch.
    """
    values: dict[str, object] = {
        "openai_status": openai_status,
        "last_polled_at": last_polled_at,
        "local_status": local_status,
    }
    if request_counts_total is not None:
        values["request_counts_total"] = request_counts_total
    if request_counts_completed is not None:
        values["request_counts_completed"] = request_counts_completed
    if request_counts_failed is not None:
        values["request_counts_failed"] = request_counts_failed

    # Guard against racing the stale-batch detector: never overwrite a row that is
    # mid-expiry (``expiring``) or already terminal.
    guarded = ARENA_AI_BATCH_JOB_TERMINAL_STATUSES | {ArenaAIBatchJobStatus.EXPIRING.value}
    stmt = (
        sa.update(arena_ai_batch_jobs)
        .where(arena_ai_batch_jobs.c.openai_batch_id == openai_batch_id)
        .where(arena_ai_batch_jobs.c.local_status.not_in(guarded))
        .values(**values)
    )
    result = await conn.execute(stmt)
    return result.rowcount


async def finalize_batch_job(
    conn: AsyncConnection,
    openai_batch_id: str,
    local_status: str,
    completed_at: datetime,
    error_file_id: str | None = None,
    last_error: str | None = None,
) -> None:
    """Mark a batch job as terminal and record completion metadata.

    Args:
        conn: Active async database connection (within a transaction).
        openai_batch_id: OpenAI batch identifier to match.
        local_status: Terminal local status (completed, failed, expired, cancelled).
        completed_at: Timestamp when the terminal status was reached.
        error_file_id: OpenAI error output file ID, or ``None`` when absent.
        last_error: Error message from OpenAI or local processing, or ``None``.
    """
    values: dict[str, object] = {
        "local_status": local_status,
        "completed_at": completed_at,
    }
    if error_file_id is not None:
        values["error_file_id"] = error_file_id
    if last_error is not None:
        values["last_error"] = last_error

    stmt = (
        sa.update(arena_ai_batch_jobs).where(arena_ai_batch_jobs.c.openai_batch_id == openai_batch_id).values(**values)
    )
    await conn.execute(stmt)


async def record_batch_cleanup_error(
    conn: AsyncConnection,
    openai_batch_id: str,
    last_error: str,
) -> None:
    """Persist a cleanup (cancellation/file-deletion) failure to ``last_error``.

    Provides a durable record for operators when a stale batch could not be
    cancelled or its files could not be deleted. Applies to every row sharing the
    ``openai_batch_id`` regardless of status, since these rows are already terminal.

    Args:
        conn: Active async database connection (within a transaction).
        openai_batch_id: OpenAI batch identifier to match.
        last_error: Human-readable cleanup error to record.
    """
    stmt = (
        sa.update(arena_ai_batch_jobs)
        .where(arena_ai_batch_jobs.c.openai_batch_id == openai_batch_id)
        .values(last_error=last_error)
    )
    await conn.execute(stmt)


async def get_stale_batch_jobs(conn: AsyncConnection, before_dt: datetime) -> list[BatchJobRow]:
    """Return all non-terminal rows of every batch whose *whole group* is stale.

    Staleness is decided per ``openai_batch_id``, not per row, so a multi-submission
    batch is never partially expired. A batch qualifies only when:

    * the **newest** of its non-terminal pollable rows was submitted at or before
      ``before_dt`` (``MAX(submitted_at) <= before_dt``) — legacy rows whose
      ``submitted_at`` was backfilled per-row from ``created_at`` therefore cannot
      cross the threshold independently of their siblings; and
    * **no** row of the batch is ``completed`` — if another worker already
      completed the batch, its results are arriving and it must not be expired or
      have its OpenAI files deleted.

    All non-terminal rows of a qualifying batch are returned — including any already
    in ``expiring`` — so the group is expired together and a crash mid-expiry is
    recovered on the next cycle. (A prior partial expiry that left a sibling
    ``expired`` does not disqualify the batch, so the stuck ``expiring`` row is
    still recovered.)

    Args:
        conn: Active async database connection.
        before_dt: Staleness threshold compared against each batch's newest row.

    Returns:
        List of ``BatchJobRow`` objects ordered by ``submitted_at`` ascending.
    """
    excluded = ARENA_AI_BATCH_JOB_TERMINAL_STATUSES | {ArenaAIBatchJobStatus.STAGED.value}
    stale_batch_ids = (
        sa.select(arena_ai_batch_jobs.c.openai_batch_id)
        .where(
            arena_ai_batch_jobs.c.openai_batch_id.is_not(None),
            arena_ai_batch_jobs.c.submitted_at.is_not(None),
            arena_ai_batch_jobs.c.local_status.not_in(excluded),
        )
        .group_by(arena_ai_batch_jobs.c.openai_batch_id)
        .having(sa.func.max(arena_ai_batch_jobs.c.submitted_at) <= before_dt)
    )
    completed_batch_ids = sa.select(arena_ai_batch_jobs.c.openai_batch_id).where(
        arena_ai_batch_jobs.c.local_status == ArenaAIBatchJobStatus.COMPLETED.value
    )
    stmt = (
        sa.select(
            arena_ai_batch_jobs.c.id,
            arena_ai_batch_jobs.c.submission_id,
            arena_ai_batch_jobs.c.openai_batch_id,
            arena_ai_batch_jobs.c.local_status,
            arena_ai_batch_jobs.c.input_file_id,
            arena_ai_batch_jobs.c.code_file_id,
            arena_ai_batch_jobs.c.statement_file_id,
            arena_ai_batch_jobs.c.error_file_id,
            arena_ai_batch_jobs.c.submitted_at,
        )
        .where(
            arena_ai_batch_jobs.c.openai_batch_id.in_(stale_batch_ids),
            arena_ai_batch_jobs.c.openai_batch_id.not_in(completed_batch_ids),
            arena_ai_batch_jobs.c.local_status.not_in(excluded),
        )
        .order_by(arena_ai_batch_jobs.c.submitted_at.asc())
    )
    rows = (await conn.execute(stmt)).mappings().all()
    return [_row_to_dataclass(r) for r in rows]


async def claim_batch_job_for_expiry(conn: AsyncConnection, submission_id: str) -> bool:
    """Atomically claim a stale batch job by transitioning it to ``expiring``.

    The conditional UPDATE ... RETURNING only matches rows that are not already
    terminal. Two concurrent claims cannot both succeed: the row lock serializes
    them and, once the first transaction commits the finalize (``expired``), the
    second sees a terminal row and matches nothing. A row left in ``expiring`` by a
    crashed transaction is intentionally re-claimable so the next cycle recovers it.

    Args:
        conn: Active async database connection (within a transaction).
        submission_id: UUID of the ``arena_submissions`` row.

    Returns:
        ``True`` when this call claimed the row, ``False`` when it was already
        terminal or claimed by another process.
    """
    blocked = ARENA_AI_BATCH_JOB_TERMINAL_STATUSES
    stmt = (
        sa.update(arena_ai_batch_jobs)
        .where(
            arena_ai_batch_jobs.c.submission_id == submission_id,
            arena_ai_batch_jobs.c.local_status.not_in(blocked),
        )
        .values(local_status=ArenaAIBatchJobStatus.EXPIRING.value)
        .returning(arena_ai_batch_jobs.c.id)
    )
    row = (await conn.execute(stmt)).first()
    return row is not None


async def finalize_expired_batch_job(
    conn: AsyncConnection,
    submission_id: str,
    completed_at: datetime,
) -> None:
    """Transition a claimed (``expiring``) batch job to the terminal ``expired`` status.

    Only matches the row this transaction claimed (``local_status='expiring'``), so a
    concurrent finalize cannot double-apply.

    Args:
        conn: Active async database connection (within a transaction).
        submission_id: UUID of the ``arena_submissions`` row.
        completed_at: Timestamp when the local expiry was finalized.
    """
    stmt = (
        sa.update(arena_ai_batch_jobs)
        .where(
            arena_ai_batch_jobs.c.submission_id == submission_id,
            arena_ai_batch_jobs.c.local_status == ArenaAIBatchJobStatus.EXPIRING.value,
        )
        .values(
            local_status=ArenaAIBatchJobStatus.EXPIRED.value,
            completed_at=completed_at,
        )
    )
    await conn.execute(stmt)


def _row_to_dataclass(row: sa.RowMapping) -> BatchJobRow:
    """Convert a SQLAlchemy row mapping to a ``BatchJobRow`` dataclass.

    Args:
        row: Mapping returned by ``.mappings()`` from a SELECT on ``arena_ai_batch_jobs``.

    Returns:
        Populated ``BatchJobRow`` instance.
    """
    return BatchJobRow(
        id=str(row["id"]),
        submission_id=str(row["submission_id"]),
        openai_batch_id=str(row["openai_batch_id"]) if row["openai_batch_id"] else None,
        local_status=str(row["local_status"]),
        input_file_id=str(row["input_file_id"]) if row["input_file_id"] else None,
        code_file_id=str(row["code_file_id"]) if row["code_file_id"] else None,
        statement_file_id=str(row["statement_file_id"]) if row["statement_file_id"] else None,
        error_file_id=str(row["error_file_id"]) if row["error_file_id"] else None,
        submitted_at=row.get("submitted_at"),
    )
