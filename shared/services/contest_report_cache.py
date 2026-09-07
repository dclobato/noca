#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared keys and invalidation for the Web contest-report cache."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

_PREFIX = "noca:web:contest-report"
PAYLOAD_VERSION = 2


def contest_report_generation_key(contest_id: str) -> str:
    """Return the key holding the current report-cache generation."""
    return f"{_PREFIX}:generation:{contest_id}"


def contest_report_data_key(contest_id: str, generation: str, site_id: str | None) -> str:
    """Return the key holding one contest/site report payload."""
    scope = site_id or "all"
    return f"{_PREFIX}:v{PAYLOAD_VERSION}:{contest_id}:{generation}:{scope}"


async def get_contest_report_generation(valkey: Any, contest_id: str) -> str | None:
    """Read the current generation, using ``initial`` before the first invalidation.

    ``None`` means Valkey could not be read. Callers must bypass every cache
    layer in that case so a cache outage can never make the report unavailable.
    """
    try:
        value = await valkey.get(contest_report_generation_key(contest_id))
    except Exception as exc:  # noqa: BLE001 - cache failure must not fail a report
        logger.warning("Contest report generation read failed for %s: %s", contest_id, exc)
        return None
    if value is None:
        is_available = getattr(valkey, "is_available", True)
        return "initial" if is_available else None
    return value.decode() if isinstance(value, bytes) else str(value)


async def invalidate_contest_report_cache(valkey: Any, contest_id: str) -> None:
    """Rotate a contest's report generation after a report-relevant commit.

    Existing data keys are deliberately left to expire. A builder racing this
    rotation can therefore finish safely under its old generation without ever
    becoming visible as the current report.
    """
    if valkey is None:
        return
    try:
        await valkey.set(contest_report_generation_key(contest_id), uuid4().hex)
    except Exception as exc:  # noqa: BLE001 - invalidation is best-effort
        logger.warning("Contest report invalidation failed for %s: %s", contest_id, exc)
