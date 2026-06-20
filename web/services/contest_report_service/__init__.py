#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Pure contest report aggregation service."""

from .common import ALL_VERDICTS
from .computation import compute_contest_report
from .models import (
    CellValue,
    ContestReport,
    DistributionRow,
    LanguageInfo,
    ProblemInfo,
    ProblemSummaryRow,
    TeamRow,
    TimeWindow,
)

__all__ = [
    "ALL_VERDICTS",
    "CellValue",
    "ContestReport",
    "DistributionRow",
    "LanguageInfo",
    "ProblemInfo",
    "ProblemSummaryRow",
    "TeamRow",
    "TimeWindow",
    "compute_contest_report",
]
