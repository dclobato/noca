#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ICPC scoreboard service package.

The scoreboard DTOs and the pure scoring implementation live in
``shared/services/scoreboard_projection.py``; this package only adapts web
queries, caching, and orchestration, re-exporting the shared DTOs for
backwards compatibility.
"""

from shared.services.scoreboard_projection import ProblemResult, ScoreboardSnapshot, TeamStanding

from .service import ScoreboardService

__all__ = [
    "ProblemResult",
    "ScoreboardService",
    "ScoreboardSnapshot",
    "TeamStanding",
]
