#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
shared/services/scoreboard_cache.py

Scoreboard cache key helpers shared by the web layer and the autojudge worker.

Both runtimes need to invalidate the scoreboard cache when a new verdict is
produced. Centralizing the key logic here ensures both sides always use the
same keys.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_PREFIX = "scoreboard"


def scoreboard_full_key(contest_id: str) -> str:
    """Cache key for the admin/judge view (all submissions visible)."""
    return f"{_PREFIX}:{contest_id}:full"


def scoreboard_public_key(contest_id: str) -> str:
    """Cache key for the public/team view (frozen after stop_updating_scoreboard)."""
    return f"{_PREFIX}:{contest_id}:public"


def scoreboard_frozen_key(contest_id: str) -> str:
    """Cache key for the public view locked at freeze time (no TTL).

    Written once when the scoreboard first freezes and kept until the admin
    releases the final standings or changes ``stop_updating_scoreboard``.
    Regular cache invalidation (new verdicts, new submissions) does NOT touch
    this key so the snapshot remains completely static during the freeze window.
    """
    return f"{_PREFIX}:{contest_id}:frozen"


def scoreboard_final_key(contest_id: str) -> str:
    """Cache key for the permanently released final scoreboard (no TTL)."""
    return f"{_PREFIX}:{contest_id}:final"


async def invalidate_scoreboard_cache(valkey: Any, contest_id: str) -> None:
    """Delete both scoreboard cache keys for *contest_id*. Best-effort.

    Accepts any object with an async ``delete(*keys)`` method — works with
    both ``shared.services.valkey_service.ValkeyRuntime`` and a raw
    ``valkey.asyncio.Valkey`` client.

    Args:
        valkey: A ValkeyRuntime instance or raw async Valkey client.
        contest_id: The contest UUID string whose cache should be cleared.
    """
    try:
        await valkey.delete(scoreboard_full_key(contest_id), scoreboard_public_key(contest_id))
    except Exception as exc:
        logger.warning(
            f"Scoreboard cache for contest 'contest_id' invalidation failed (best-effort, ignored): {str(exc)}"
        )
