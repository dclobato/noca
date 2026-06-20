#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Read AI batch turnaround statistics for Arena views."""

from __future__ import annotations

import logging

from pydantic import ValidationError

from shared.queue_schema import AIBatchTurnaroundStats
from shared.services.valkey_service import AI_BATCH_TURNAROUND_STATS_KEY, ValkeyRuntime

logger = logging.getLogger(__name__)


async def get_batch_turnaround_stats(
    valkey_runtime: ValkeyRuntime,
) -> AIBatchTurnaroundStats | None:
    """Return validated recent batch-review turnaround statistics.

    Args:
        valkey_runtime: Active Arena Valkey runtime.

    Returns:
        Validated statistics, or ``None`` when the key is absent or invalid.
    """
    raw_stats = await valkey_runtime.get(AI_BATCH_TURNAROUND_STATS_KEY)
    if raw_stats is None:
        return None

    try:
        return AIBatchTurnaroundStats.model_validate_json(raw_stats)
    except ValidationError:
        logger.warning("Ignoring invalid AI batch turnaround statistics payload")
        return None
