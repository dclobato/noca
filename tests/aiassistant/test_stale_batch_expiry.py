#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the stale-batch detector (aiassistant.batch_results.handle_stale_batch
and aiassistant.batch_poller._expire_stale_batches).

All database tests use the shared SQLite engine fixture from conftest.py.
OpenAI calls are always mocked — no real API calls are made.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa

from aiassistant import batch_poller, batch_results
from aiassistant.db.batch_queries import get_pending_batch_jobs, update_batch_job_poll
from shared.db_schema import (
    arena_ai_batch_jobs,
    arena_ai_credit_transactions,
    arena_notifications,
    arena_problems,
    arena_submissions,
    arena_users,
    languages,
)
from shared.enumerations import ArenaAIBatchJobStatus, ArenaNotificationKind, ArenaRole

_LANGUAGE_ID = "python3"
_LOG = logging.getLogger("test_stale_batch_expiry")


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_submission(
    engine: object,
    *,
    submission_id: str,
    user_id: str,
    problem_id: str,
    credits: int = 0,
    arena_number: int | None = None,
) -> None:
    """Insert the minimal Arena graph with a configurable starting credit balance."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        with contextlib.suppress(Exception):
            await conn.execute(
                languages.insert().values(
                    id=_LANGUAGE_ID,
                    name="Python 3",
                    icon="python",
                    compile_image="noca/python:compile",
                    run_image="noca/python:run",
                    compile_cmd=None,
                    run_cmd=["python3", "main.py"],
                    source_filename="main.py",
                    artifact_path="/sandbox/main.py",
                    artifact_is_source=True,
                    compile_timeout_s=10.0,
                    profiling_repetitions_default=3,
                    profiled_pids_floor=32,
                    active=True,
                )
            )
        await conn.execute(
            arena_users.insert().values(
                id=user_id,
                nome="Stale Test User",
                dta_nascimento=date(1995, 1, 1),
                email_normalizado=f"{user_id}@test.example.com",
                password_hash="hash",
                role=ArenaRole.ARENA_USER.value,
                _ai_api_key=None,
                ai_backend_credits=credits,
            )
        )
        await conn.execute(
            arena_problems.insert().values(
                id=problem_id,
                arena_number=arena_number if arena_number is not None else abs(hash(problem_id)) % 100000 + 1,
                title="Stale Problem",
                owner_id=user_id,
                problem_statement="# Statement\nSolve it.",
                enabled=True,
                problem_image_base64=None,
                problem_image_mime=None,
                problem_image_caption=None,
            )
        )
        await conn.execute(
            arena_submissions.insert().values(
                id=submission_id,
                user_id=user_id,
                problem_id=problem_id,
                language_id=_LANGUAGE_ID,
                source_code="print('hello')",
                source_hash="a" * 64,
                source_size_bytes=14,
                submit_to_ai=True,
            )
        )


async def _insert_batch_job(
    engine: object,
    *,
    submission_id: str,
    openai_batch_id: str,
    submitted_at: datetime | None,
    local_status: str = ArenaAIBatchJobStatus.POLLING.value,
) -> None:
    """Insert an arena_ai_batch_jobs row with an explicit submitted_at."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        await conn.execute(
            arena_ai_batch_jobs.insert().values(
                id=str(uuid.uuid4()),
                submission_id=submission_id,
                openai_batch_id=openai_batch_id,
                local_status=local_status,
                input_file_id="file-input-001",
                code_file_id="file-code-001",
                statement_file_id="file-stmt-001",
                submitted_at=submitted_at,
            )
        )


def _old() -> datetime:
    """A submitted_at well past the default 24 h staleness threshold."""
    return datetime.now(UTC) - timedelta(hours=48)


def _fresh() -> datetime:
    """A submitted_at within the staleness threshold."""
    return datetime.now(UTC) - timedelta(hours=1)


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------


async def _fetch_batch_job(engine: object, submission_id: str) -> sa.RowMapping | None:
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        return (
            (
                await conn.execute(
                    sa.select(arena_ai_batch_jobs).where(arena_ai_batch_jobs.c.submission_id == submission_id)
                )
            )
            .mappings()
            .first()
        )


async def _fetch_credits(engine: object, user_id: str) -> int:
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        return int(
            (
                await conn.execute(sa.select(arena_users.c.ai_backend_credits).where(arena_users.c.id == user_id))
            ).scalar_one()
        )


async def _count_refund_txns(engine: object, submission_id: str) -> int:
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        return int(
            (
                await conn.execute(
                    sa.select(sa.func.count())
                    .select_from(arena_ai_credit_transactions)
                    .where(
                        arena_ai_credit_transactions.c.submission_id == submission_id,
                        arena_ai_credit_transactions.c.transaction_type == "refund",
                    )
                )
            ).scalar_one()
        )


async def _fetch_submit_to_ai(engine: object, submission_id: str) -> bool | None:
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    sa.select(arena_submissions.c.submit_to_ai).where(arena_submissions.c.id == submission_id)
                )
            )
            .mappings()
            .first()
        )
    return bool(row["submit_to_ai"]) if row is not None else None


async def _count_notifications(engine: object, submission_id: str) -> int:
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        return int(
            (
                await conn.execute(
                    sa.select(sa.func.count())
                    .select_from(arena_notifications)
                    .where(
                        arena_notifications.c.source_ref == submission_id,
                        arena_notifications.c.notification_kind == ArenaNotificationKind.AI_REVIEW_FAILED.value,
                    )
                )
            ).scalar_one()
        )


# ---------------------------------------------------------------------------
# Tests — handle_stale_batch via _expire_stale_batches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_users_same_stale_batch(engine: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two users sharing one openai_batch_id: both expired, refunded, and notified."""
    batch_id = "batch-shared-001"
    await _seed_submission(engine, submission_id="s1", user_id="u1", problem_id="p1", credits=0)
    await _seed_submission(engine, submission_id="s2", user_id="u2", problem_id="p2", credits=2)
    await _insert_batch_job(engine, submission_id="s1", openai_batch_id=batch_id, submitted_at=_old())
    await _insert_batch_job(engine, submission_id="s2", openai_batch_id=batch_id, submitted_at=_old())

    monkeypatch.setattr(batch_poller.settings, "OPENAI_API_KEY", None)
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    await batch_poller._expire_stale_batches(db_engine, _LOG)

    for sid, uid, expected_credits in (("s1", "u1", 1), ("s2", "u2", 3)):
        row = await _fetch_batch_job(engine, sid)
        assert row is not None
        assert row["local_status"] == ArenaAIBatchJobStatus.EXPIRED.value
        assert await _fetch_credits(engine, uid) == expected_credits
        assert await _count_refund_txns(engine, sid) == 1
        assert await _count_notifications(engine, sid) == 1


@pytest.mark.asyncio
async def test_claim_prevents_double_refund(engine: object) -> None:
    """Two concurrent workers racing the same row: exactly one refund total.

    Both ``handle_stale_batch`` calls run at once via ``asyncio.gather``. The
    atomic claim (UPDATE ... RETURNING under a row/DB lock) lets only one win; the
    loser's claim matches a now-terminal row and returns no claimed jobs.
    """
    import asyncio

    await _seed_submission(engine, submission_id="s1", user_id="u1", problem_id="p1", credits=0)
    await _insert_batch_job(engine, submission_id="s1", openai_batch_id="batch-001", submitted_at=_old())

    from aiassistant.db.batch_queries import BatchJobRow

    job = BatchJobRow(
        id="j1",
        submission_id="s1",
        openai_batch_id="batch-001",
        local_status=ArenaAIBatchJobStatus.POLLING.value,
        input_file_id=None,
        code_file_id=None,
        statement_file_id=None,
        error_file_id=None,
        submitted_at=_old(),
    )

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    results = await asyncio.gather(
        batch_results.handle_stale_batch(db_engine, [job], _LOG),
        batch_results.handle_stale_batch(db_engine, [job], _LOG),
    )

    # Exactly one of the two racers claimed the row.
    assert sorted(len(r) for r in results) == [0, 1]
    assert await _fetch_credits(engine, "u1") == 1
    assert await _count_refund_txns(engine, "s1") == 1


@pytest.mark.asyncio
async def test_partial_batch_not_expired(engine: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """A batch with one old and one fresh sibling is NOT expired (no partial expiry).

    Staleness is decided per batch (MAX(submitted_at) <= threshold), so the fresh
    sibling keeps the whole shared OpenAI batch out of the stale set.
    """
    batch_id = "batch-mixed-001"
    await _seed_submission(engine, submission_id="s1", user_id="u1", problem_id="p1", credits=0)
    await _seed_submission(engine, submission_id="s2", user_id="u2", problem_id="p2", credits=0)
    await _insert_batch_job(engine, submission_id="s1", openai_batch_id=batch_id, submitted_at=_old())
    await _insert_batch_job(engine, submission_id="s2", openai_batch_id=batch_id, submitted_at=_fresh())

    monkeypatch.setattr(batch_poller.settings, "OPENAI_API_KEY", None)
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    await batch_poller._expire_stale_batches(db_engine, _LOG)

    for sid in ("s1", "s2"):
        row = await _fetch_batch_job(engine, sid)
        assert row is not None
        assert row["local_status"] == ArenaAIBatchJobStatus.POLLING.value
        assert await _count_refund_txns(engine, sid) == 0


@pytest.mark.asyncio
async def test_completed_sibling_leaves_whole_batch_alone(engine: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """A batch with a completed sibling is not expired, refunded, or cancelled.

    If another worker already completed the batch, its results are arriving — the
    stale detector must leave every row and the OpenAI batch untouched.
    """
    batch_id = "batch-partial-001"
    await _seed_submission(engine, submission_id="s1", user_id="u1", problem_id="p1", credits=0)
    await _seed_submission(engine, submission_id="s2", user_id="u2", problem_id="p2", credits=0)
    # s1 is old-and-polling; s2 was already completed by another worker.
    await _insert_batch_job(engine, submission_id="s1", openai_batch_id=batch_id, submitted_at=_old())
    await _insert_batch_job(
        engine,
        submission_id="s2",
        openai_batch_id=batch_id,
        submitted_at=_old(),
        local_status=ArenaAIBatchJobStatus.COMPLETED.value,
    )

    monkeypatch.setattr(batch_poller.settings, "OPENAI_API_KEY", "sk-platform")
    client = MagicMock()
    client.batches.cancel = AsyncMock(return_value=None)
    client.files.delete = AsyncMock(return_value=None)

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    with patch("aiassistant.batch_poller.AsyncOpenAI", return_value=client):
        await batch_poller._expire_stale_batches(db_engine, _LOG)

    # s1 untouched (still polling), no refund, batch not cancelled.
    row = await _fetch_batch_job(engine, "s1")
    assert row is not None
    assert row["local_status"] == ArenaAIBatchJobStatus.POLLING.value
    assert await _count_refund_txns(engine, "s1") == 0
    client.batches.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_does_not_complete_expired_batch(engine: object) -> None:
    """A completed OpenAI status does not store a review over an already-expired row."""
    from aiassistant.db.batch_queries import BatchJobRow

    await _seed_submission(engine, submission_id="s1", user_id="u1", problem_id="p1", credits=0)
    await _insert_batch_job(
        engine,
        submission_id="s1",
        openai_batch_id="batch-001",
        submitted_at=_old(),
        local_status=ArenaAIBatchJobStatus.EXPIRED.value,
    )

    job = BatchJobRow(
        id="j1",
        submission_id="s1",
        openai_batch_id="batch-001",
        local_status=ArenaAIBatchJobStatus.EXPIRED.value,
        input_file_id="file-input-001",
        code_file_id="file-code-001",
        statement_file_id="file-stmt-001",
        error_file_id=None,
        submitted_at=_old(),
    )

    batch = MagicMock()
    batch.status = "completed"
    batch.output_file_id = "file-out-001"
    batch.error_file_id = None
    rc = MagicMock()
    rc.total, rc.completed, rc.failed = 1, 1, 0
    batch.request_counts = rc
    client = MagicMock()
    client.batches.retrieve = AsyncMock(return_value=batch)
    client.files.content = AsyncMock()
    client.files.delete = AsyncMock()

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    await batch_poller._process_batch(db_engine, client, "batch-001", [job], _LOG)

    # No review stored; row stays expired (guard + rowcount bail).
    async with db_engine.begin() as conn:
        from shared.db_schema import arena_submission_ai_reviews

        review = (
            (
                await conn.execute(
                    sa.select(arena_submission_ai_reviews).where(arena_submission_ai_reviews.c.submission_id == "s1")
                )
            )
            .mappings()
            .first()
        )
    assert review is None
    row = await _fetch_batch_job(engine, "s1")
    assert row is not None
    assert row["local_status"] == ArenaAIBatchJobStatus.EXPIRED.value


@pytest.mark.asyncio
async def test_rollback_on_refund_failure(engine: object) -> None:
    """A refund error rolls back the whole transaction; row stays pre-claim."""
    await _seed_submission(engine, submission_id="s1", user_id="u1", problem_id="p1", credits=0)
    await _insert_batch_job(engine, submission_id="s1", openai_batch_id="batch-001", submitted_at=_old())

    from aiassistant.db.batch_queries import BatchJobRow

    job = BatchJobRow(
        id="j1",
        submission_id="s1",
        openai_batch_id="batch-001",
        local_status=ArenaAIBatchJobStatus.POLLING.value,
        input_file_id=None,
        code_file_id=None,
        statement_file_id=None,
        error_file_id=None,
        submitted_at=_old(),
    )

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    with (
        patch.object(
            batch_results,
            "refund_ai_credit_for_submission",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
        contextlib.suppress(RuntimeError),
    ):
        await batch_results.handle_stale_batch(db_engine, [job], _LOG)

    row = await _fetch_batch_job(engine, "s1")
    assert row is not None
    assert row["local_status"] == ArenaAIBatchJobStatus.POLLING.value
    assert await _fetch_submit_to_ai(engine, "s1") is True
    assert await _fetch_credits(engine, "u1") == 0
    assert await _count_refund_txns(engine, "s1") == 0


@pytest.mark.asyncio
async def test_rollback_on_finalize_failure(engine: object) -> None:
    """A finalize error rolls back the refund; no partial credit committed."""
    await _seed_submission(engine, submission_id="s1", user_id="u1", problem_id="p1", credits=0)
    await _insert_batch_job(engine, submission_id="s1", openai_batch_id="batch-001", submitted_at=_old())

    from aiassistant.db.batch_queries import BatchJobRow

    job = BatchJobRow(
        id="j1",
        submission_id="s1",
        openai_batch_id="batch-001",
        local_status=ArenaAIBatchJobStatus.POLLING.value,
        input_file_id=None,
        code_file_id=None,
        statement_file_id=None,
        error_file_id=None,
        submitted_at=_old(),
    )

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    with (
        patch.object(
            batch_results,
            "finalize_expired_batch_job",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
        contextlib.suppress(RuntimeError),
    ):
        await batch_results.handle_stale_batch(db_engine, [job], _LOG)

    row = await _fetch_batch_job(engine, "s1")
    assert row is not None
    assert row["local_status"] == ArenaAIBatchJobStatus.POLLING.value
    assert await _fetch_credits(engine, "u1") == 0
    assert await _count_refund_txns(engine, "s1") == 0


@pytest.mark.asyncio
async def test_fresh_batch_not_expired(engine: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """A batch submitted within the threshold is not expired."""
    await _seed_submission(engine, submission_id="s1", user_id="u1", problem_id="p1", credits=0)
    await _insert_batch_job(engine, submission_id="s1", openai_batch_id="batch-001", submitted_at=_fresh())

    monkeypatch.setattr(batch_poller.settings, "OPENAI_API_KEY", None)
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    await batch_poller._expire_stale_batches(db_engine, _LOG)

    row = await _fetch_batch_job(engine, "s1")
    assert row is not None
    assert row["local_status"] == ArenaAIBatchJobStatus.POLLING.value
    assert await _fetch_credits(engine, "u1") == 0


@pytest.mark.asyncio
async def test_staged_row_excluded(engine: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """A staged row (no openai_batch_id) is ignored even with an old submitted_at-like age."""
    await _seed_submission(engine, submission_id="s1", user_id="u1", problem_id="p1", credits=0)
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        await conn.execute(
            arena_ai_batch_jobs.insert().values(
                id=str(uuid.uuid4()),
                submission_id="s1",
                openai_batch_id=None,
                local_status=ArenaAIBatchJobStatus.STAGED.value,
                submitted_at=None,
            )
        )

    monkeypatch.setattr(batch_poller.settings, "OPENAI_API_KEY", None)
    await batch_poller._expire_stale_batches(db_engine, _LOG)

    row = await _fetch_batch_job(engine, "s1")
    assert row is not None
    assert row["local_status"] == ArenaAIBatchJobStatus.STAGED.value


@pytest.mark.asyncio
async def test_deleted_submission_still_finalizes(engine: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing submission still finalizes the batch row as expired; no refund."""
    await _seed_submission(engine, submission_id="s1", user_id="u1", problem_id="p1", credits=0)
    await _insert_batch_job(engine, submission_id="s1", openai_batch_id="batch-001", submitted_at=_old())

    # Delete the submission row (cascades remove the batch FK? No — keep batch row).
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        # Detach batch row from the submission so deleting the submission leaves the
        # batch row present (simulating a row whose submission is gone).
        await conn.execute(
            arena_ai_batch_jobs.update()
            .where(arena_ai_batch_jobs.c.submission_id == "s1")
            .values(submission_id="s-missing")
        )

    job_id = "s-missing"
    monkeypatch.setattr(batch_poller.settings, "OPENAI_API_KEY", None)
    await batch_poller._expire_stale_batches(db_engine, _LOG)

    row = await _fetch_batch_job(engine, job_id)
    assert row is not None
    assert row["local_status"] == ArenaAIBatchJobStatus.EXPIRED.value
    assert await _count_refund_txns(engine, job_id) == 0


@pytest.mark.asyncio
async def test_submit_to_ai_cleared_on_expiry(engine: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """submit_to_ai is False after a stale expiry so the button is re-enabled."""
    await _seed_submission(engine, submission_id="s1", user_id="u1", problem_id="p1", credits=0)
    await _insert_batch_job(engine, submission_id="s1", openai_batch_id="batch-001", submitted_at=_old())

    monkeypatch.setattr(batch_poller.settings, "OPENAI_API_KEY", None)
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    await batch_poller._expire_stale_batches(db_engine, _LOG)

    assert await _fetch_submit_to_ai(engine, "s1") is False


@pytest.mark.asyncio
async def test_all_rows_terminal_after_expiry(engine: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """get_pending_batch_jobs returns empty after a stale expiry."""
    await _seed_submission(engine, submission_id="s1", user_id="u1", problem_id="p1", credits=0)
    await _insert_batch_job(engine, submission_id="s1", openai_batch_id="batch-001", submitted_at=_old())

    monkeypatch.setattr(batch_poller.settings, "OPENAI_API_KEY", None)
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    await batch_poller._expire_stale_batches(db_engine, _LOG)

    async with db_engine.connect() as conn:
        assert await get_pending_batch_jobs(conn) == []


@pytest.mark.asyncio
async def test_expiring_row_recovered_on_retry(engine: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """A row stuck in 'expiring' (simulated crash) is finalized on the next cycle."""
    await _seed_submission(engine, submission_id="s1", user_id="u1", problem_id="p1", credits=0)
    await _insert_batch_job(
        engine,
        submission_id="s1",
        openai_batch_id="batch-001",
        submitted_at=_old(),
        local_status=ArenaAIBatchJobStatus.EXPIRING.value,
    )

    monkeypatch.setattr(batch_poller.settings, "OPENAI_API_KEY", None)
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    await batch_poller._expire_stale_batches(db_engine, _LOG)

    row = await _fetch_batch_job(engine, "s1")
    assert row is not None
    assert row["local_status"] == ArenaAIBatchJobStatus.EXPIRED.value
    assert await _fetch_credits(engine, "u1") == 1


@pytest.mark.asyncio
async def test_update_batch_job_poll_skips_expiring(engine: object) -> None:
    """update_batch_job_poll does not update a row in expiring or terminal status."""
    await _seed_submission(engine, submission_id="s1", user_id="u1", problem_id="p1", credits=0)
    await _insert_batch_job(
        engine,
        submission_id="s1",
        openai_batch_id="batch-001",
        submitted_at=_old(),
        local_status=ArenaAIBatchJobStatus.EXPIRING.value,
    )

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        await update_batch_job_poll(
            conn,
            openai_batch_id="batch-001",
            openai_status="in_progress",
            last_polled_at=datetime.now(UTC),
            local_status=ArenaAIBatchJobStatus.POLLING.value,
        )

    row = await _fetch_batch_job(engine, "s1")
    assert row is not None
    # Unchanged — guard prevented the update.
    assert row["local_status"] == ArenaAIBatchJobStatus.EXPIRING.value
    assert row["openai_status"] is None


@pytest.mark.asyncio
async def test_no_openai_key_skips_cleanup_but_expires(engine: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no platform key, DB expiry still commits; cancel/cleanup is skipped."""
    await _seed_submission(engine, submission_id="s1", user_id="u1", problem_id="p1", credits=0)
    await _insert_batch_job(engine, submission_id="s1", openai_batch_id="batch-001", submitted_at=_old())

    monkeypatch.setattr(batch_poller.settings, "OPENAI_API_KEY", None)
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    with patch("aiassistant.batch_poller.AsyncOpenAI") as mock_ctor:
        await batch_poller._expire_stale_batches(db_engine, _LOG)

    mock_ctor.assert_not_called()
    row = await _fetch_batch_job(engine, "s1")
    assert row is not None
    assert row["local_status"] == ArenaAIBatchJobStatus.EXPIRED.value


@pytest.mark.asyncio
async def test_platform_key_cancels_and_deletes_files(engine: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """With a platform key, the OpenAI batch is cancelled and files deleted."""
    await _seed_submission(engine, submission_id="s1", user_id="u1", problem_id="p1", credits=0)
    await _insert_batch_job(engine, submission_id="s1", openai_batch_id="batch-001", submitted_at=_old())

    monkeypatch.setattr(batch_poller.settings, "OPENAI_API_KEY", "sk-platform")
    client = MagicMock()
    client.batches.cancel = AsyncMock(return_value=None)
    client.files.delete = AsyncMock(return_value=None)

    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    with patch("aiassistant.batch_poller.AsyncOpenAI", return_value=client):
        await batch_poller._expire_stale_batches(db_engine, _LOG)

    client.batches.cancel.assert_awaited_once_with("batch-001")
    deleted = {call.args[0] for call in client.files.delete.await_args_list}
    assert {"file-input-001", "file-code-001", "file-stmt-001"} <= deleted
    row = await _fetch_batch_job(engine, "s1")
    assert row is not None
    assert row["local_status"] == ArenaAIBatchJobStatus.EXPIRED.value
