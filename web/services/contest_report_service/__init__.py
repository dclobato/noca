#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Pure contest report aggregation service."""

from .common import ALL_VERDICTS, compute_time_window_minutes
from .computation import compute_contest_report
from .models import (
    ActiveTeamsHighlight,
    CellValue,
    ContestReport,
    DistributionRow,
    FiveNumberSummary,
    Highlights,
    LanguageHighlight,
    LanguageInfo,
    PerformanceSummary,
    ProblemHighlight,
    ProblemInfo,
    ProblemRaceSeries,
    ProblemSummaryRow,
    SolvedCountBucket,
    SolveMetrics,
    TeamRow,
    TimeWindow,
)

__all__ = [
    "ALL_VERDICTS",
    "ActiveTeamsHighlight",
    "CellValue",
    "ContestReport",
    "DistributionRow",
    "FiveNumberSummary",
    "Highlights",
    "LanguageHighlight",
    "LanguageInfo",
    "PerformanceSummary",
    "ProblemHighlight",
    "ProblemInfo",
    "ProblemRaceSeries",
    "ProblemSummaryRow",
    "SolveMetrics",
    "SolvedCountBucket",
    "TeamRow",
    "TimeWindow",
    "compute_contest_report",
    "compute_time_window_minutes",
]
