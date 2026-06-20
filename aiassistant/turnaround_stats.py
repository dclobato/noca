#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Compute and publish recent AI batch turnaround statistics."""

from __future__ import annotations

import logging
import statistics
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from shared.db_schema import arena_ai_batch_jobs, arena_submission_ai_reviews
from shared.enumerations import ArenaAIBatchJobStatus
from shared.queue_schema import AIBatchTurnaroundStats
from shared.services.valkey_service import AI_BATCH_TURNAROUND_STATS_KEY, ValkeyRuntime

TURNAROUND_SAMPLE_LIMIT = 100


async def get_recent_batch_turnaround_seconds(
    conn: AsyncConnection,
    *,
    limit: int,
) -> list[int]:
    """Return recent successful platform-review turnaround values.

    Args:
        conn: Active async database connection.
        limit: Maximum number of recent reviews to load.

    Returns:
        Non-negative whole seconds from batch staging to review storage, ordered
        from newest to oldest review.
    """
    stmt = (
        select(
            arena_ai_batch_jobs.c.created_at,
            arena_submission_ai_reviews.c.ai_response_at,
        )
        .select_from(
            arena_ai_batch_jobs.join(
                arena_submission_ai_reviews,
                arena_submission_ai_reviews.c.submission_id == arena_ai_batch_jobs.c.submission_id,
            )
        )
        .where(
            arena_ai_batch_jobs.c.local_status == ArenaAIBatchJobStatus.COMPLETED.value,
            arena_submission_ai_reviews.c.used_platform_key.is_(True),
        )
        .order_by(
            arena_submission_ai_reviews.c.ai_response_at.desc(),
            arena_submission_ai_reviews.c.submission_id.desc(),
        )
        .limit(limit)
    )
    rows = (await conn.execute(stmt)).all()
    return [max(0, int((response_at - batch_created_at).total_seconds())) for batch_created_at, response_at in rows]


def build_turnaround_stats(
    values: list[int],
    *,
    updated_at: datetime,
) -> AIBatchTurnaroundStats | None:
    """Build the Valkey payload from recent turnaround values.

    Args:
        values: Non-negative turnaround durations in seconds.
        updated_at: UTC timestamp for payload freshness.

    Returns:
        Statistics payload, or ``None`` when no samples are available.
    """
    if not values:
        return None

    return AIBatchTurnaroundStats(
        average_seconds=round(statistics.fmean(values), 1),
        median_seconds=round(float(statistics.median(values)), 1),
        stddev_seconds=round(statistics.pstdev(values), 1) if len(values) > 1 else 0.0,
        sample_count=len(values),
        updated_at=updated_at,
    )


async def refresh_batch_turnaround_stats(
    engine: AsyncEngine,
    valkey_runtime: ValkeyRuntime,
    log: logging.Logger,
) -> AIBatchTurnaroundStats | None:
    """Recompute recent batch turnaround statistics and publish them to Valkey.

    Args:
        engine: Async SQLAlchemy engine for database access.
        valkey_runtime: Shared Valkey runtime used by the AI assistant worker.
        log: Logger for publication diagnostics.

    Returns:
        Published payload, or ``None`` when the key was removed because no
        qualifying reviews exist.
    """
    async with engine.connect() as conn:
        values = await get_recent_batch_turnaround_seconds(
            conn,
            limit=TURNAROUND_SAMPLE_LIMIT,
        )

    payload = build_turnaround_stats(values, updated_at=datetime.now(UTC))
    if payload is None:
        await valkey_runtime.delete(AI_BATCH_TURNAROUND_STATS_KEY)
        log.info("Removed AI batch turnaround statistics: no qualifying reviews")
        return None

    await valkey_runtime.set(AI_BATCH_TURNAROUND_STATS_KEY, payload.model_dump_json())
    log.info(
        "Published AI batch turnaround statistics (samples=%d, average=%.1fs, median=%.1fs, stddev=%.1fs)",
        payload.sample_count,
        payload.average_seconds,
        payload.median_seconds,
        payload.stddev_seconds,
    )
    return payload
