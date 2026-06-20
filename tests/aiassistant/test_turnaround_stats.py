#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for recent AI batch turnaround statistics."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from aiassistant.turnaround_stats import (
    build_turnaround_stats,
    get_recent_batch_turnaround_seconds,
    refresh_batch_turnaround_stats,
)
from shared.db_schema import arena_ai_batch_jobs, arena_submission_ai_reviews
from shared.enumerations import ArenaAIBatchJobStatus
from shared.services.valkey_service import AI_BATCH_TURNAROUND_STATS_KEY, ValkeyRuntime


def _make_valkey_runtime() -> MagicMock:
    """Return a mock Valkey runtime with async write methods."""
    runtime = MagicMock(spec=ValkeyRuntime)
    runtime.set = AsyncMock()
    runtime.delete = AsyncMock()
    return runtime


def test_build_turnaround_stats_computes_requested_metrics() -> None:
    """Average, median, and population standard deviation use one decimal."""
    updated_at = datetime(2026, 6, 18, tzinfo=UTC)

    stats = build_turnaround_stats([10, 20, 30, 40], updated_at=updated_at)

    assert stats is not None
    assert stats.average_seconds == 25.0
    assert stats.median_seconds == 25.0
    assert stats.stddev_seconds == 11.2
    assert stats.sample_count == 4
    assert stats.updated_at == updated_at


def test_build_turnaround_stats_single_sample_has_zero_stddev() -> None:
    """A one-value population has zero standard deviation."""
    stats = build_turnaround_stats([17], updated_at=datetime.now(UTC))

    assert stats is not None
    assert stats.average_seconds == 17.0
    assert stats.median_seconds == 17.0
    assert stats.stddev_seconds == 0.0


def test_build_turnaround_stats_empty_population_returns_none() -> None:
    """No samples produce no payload."""
    assert build_turnaround_stats([], updated_at=datetime.now(UTC)) is None


@pytest.mark.asyncio
async def test_recent_turnaround_query_limits_to_latest_100_completed_platform_reviews(
    engine: object,
) -> None:
    """The query excludes non-platform/non-completed rows and keeps the newest 100."""
    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    async with db_engine.begin() as conn:
        for index in range(102):
            submission_id = f"turnaround-{index:03d}"
            response_at = base_time + timedelta(minutes=index)
            await conn.execute(
                arena_ai_batch_jobs.insert().values(
                    id=str(uuid.uuid4()),
                    submission_id=submission_id,
                    local_status=ArenaAIBatchJobStatus.COMPLETED.value,
                    created_at=response_at - timedelta(seconds=index),
                    completed_at=response_at,
                )
            )
            await conn.execute(
                arena_submission_ai_reviews.insert().values(
                    submission_id=submission_id,
                    ai_response="Review",
                    ai_response_at=response_at,
                    _ai_review_cost=1,
                    used_platform_key=True,
                )
            )

        for suffix, status, used_platform_key in (
            ("personal", ArenaAIBatchJobStatus.COMPLETED.value, False),
            ("failed", ArenaAIBatchJobStatus.FAILED.value, True),
        ):
            submission_id = f"turnaround-{suffix}"
            response_at = base_time + timedelta(days=1)
            await conn.execute(
                arena_ai_batch_jobs.insert().values(
                    id=str(uuid.uuid4()),
                    submission_id=submission_id,
                    local_status=status,
                    created_at=response_at - timedelta(seconds=999),
                    completed_at=response_at,
                )
            )
            await conn.execute(
                arena_submission_ai_reviews.insert().values(
                    submission_id=submission_id,
                    ai_response="Review",
                    ai_response_at=response_at,
                    _ai_review_cost=1,
                    used_platform_key=used_platform_key,
                )
            )

    async with db_engine.connect() as conn:
        values = await get_recent_batch_turnaround_seconds(conn, limit=100)

    assert values == list(range(101, 1, -1))


@pytest.mark.asyncio
async def test_recent_turnaround_query_clamps_negative_duration(engine: object) -> None:
    """Clock skew cannot publish a negative turnaround."""
    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    response_at = datetime(2026, 1, 1, tzinfo=UTC)
    submission_id = "turnaround-negative"
    async with db_engine.begin() as conn:
        await conn.execute(
            arena_ai_batch_jobs.insert().values(
                id=str(uuid.uuid4()),
                submission_id=submission_id,
                local_status=ArenaAIBatchJobStatus.COMPLETED.value,
                created_at=response_at + timedelta(seconds=5),
                completed_at=response_at,
            )
        )
        await conn.execute(
            arena_submission_ai_reviews.insert().values(
                submission_id=submission_id,
                ai_response="Review",
                ai_response_at=response_at,
                _ai_review_cost=1,
                used_platform_key=True,
            )
        )

    async with db_engine.connect() as conn:
        values = await get_recent_batch_turnaround_seconds(conn, limit=100)

    assert values == [0]


@pytest.mark.asyncio
async def test_refresh_publishes_atomic_json_payload(engine: object) -> None:
    """Refresh writes the complete payload with one persistent SET operation."""
    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    runtime = _make_valkey_runtime()
    log = MagicMock()

    with patch(
        "aiassistant.turnaround_stats.get_recent_batch_turnaround_seconds",
        AsyncMock(return_value=[10, 20, 30]),
    ):
        stats = await refresh_batch_turnaround_stats(db_engine, runtime, log)

    assert stats is not None
    runtime.set.assert_awaited_once()
    key, raw_payload = runtime.set.await_args.args
    assert key == AI_BATCH_TURNAROUND_STATS_KEY
    assert runtime.set.await_args.kwargs == {}
    assert json.loads(raw_payload) == stats.model_dump(mode="json")
    runtime.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_deletes_key_for_empty_population(engine: object) -> None:
    """Refresh removes stale statistics when no qualifying reviews remain."""
    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    runtime = _make_valkey_runtime()
    log = MagicMock()

    with patch(
        "aiassistant.turnaround_stats.get_recent_batch_turnaround_seconds",
        AsyncMock(return_value=[]),
    ):
        stats = await refresh_batch_turnaround_stats(db_engine, runtime, log)

    assert stats is None
    runtime.delete.assert_awaited_once_with(AI_BATCH_TURNAROUND_STATS_KEY)
    runtime.set.assert_not_awaited()
