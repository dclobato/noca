#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Medal band resolution for the Arena leaderboard and ranking pages.

The cutoffs are deployment configuration, so this module reads them from the
settings object at call time rather than binding them once. That keeps a single
Jinja global correct for every render (and lets tests override the cutoffs).
"""

from arena.config import settings
from shared.services.balloon_assets import MedalBand, medal_band_for_rank


def arena_medal_band(rank: int) -> MedalBand | None:
    """Return the medal band for an Arena ranking position.

    Args:
        rank: The 1-based ranking position shown to the user.

    Returns:
        ``gold``, ``silver``, ``bronze``, or ``None`` when the position earns no
        medal under the configured cutoffs.
    """
    return medal_band_for_rank(
        rank,
        gold=settings.ARENA_RANKING_MEDAL_GOLD_CUTOFF,
        silver=settings.ARENA_RANKING_MEDAL_SILVER_CUTOFF,
        bronze=settings.ARENA_RANKING_MEDAL_BRONZE_CUTOFF,
    )
