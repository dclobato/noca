#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for Arena AI batch turnaround statistics reads."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from arena.services.ai_turnaround_stats_service import get_batch_turnaround_stats
from shared.queue_schema import AIBatchTurnaroundStats
from shared.services.valkey_service import AI_BATCH_TURNAROUND_STATS_KEY


@pytest.mark.asyncio
async def test_get_batch_turnaround_stats_validates_payload() -> None:
    """A valid Valkey payload is returned as the shared schema model."""
    payload = AIBatchTurnaroundStats(
        average_seconds=248.4,
        median_seconds=180,
        stddev_seconds=31.2,
        sample_count=42,
        updated_at=datetime.now(UTC),
    )
    runtime = AsyncMock()
    runtime.get.return_value = payload.model_dump_json()

    result = await get_batch_turnaround_stats(runtime)

    runtime.get.assert_awaited_once_with(AI_BATCH_TURNAROUND_STATS_KEY)
    assert result == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_stats", [None, "not-json", '{"version": 1}'])
async def test_get_batch_turnaround_stats_handles_unavailable_payload(raw_stats: str | None) -> None:
    """Missing and invalid Valkey values are presented as unavailable."""
    runtime = AsyncMock()
    runtime.get.return_value = raw_stats

    assert await get_batch_turnaround_stats(runtime) is None
