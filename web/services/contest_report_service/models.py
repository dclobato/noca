#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest report DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ProblemInfo:
    """Lightweight problem descriptor for template rendering."""

    label: str
    title: str
    color: str


@dataclass(slots=True)
class LanguageInfo:
    """Lightweight language descriptor for template rendering."""

    id: str
    name: str
    icon: str


@dataclass(slots=True)
class CellValue:
    """A single count-plus-percentage cell used in cross-tables."""

    count: int
    pct: float


@dataclass(slots=True)
class ProblemSummaryRow:
    """Row for the problem summary table."""

    problem: ProblemInfo
    total_runs: int
    ac: CellValue
    ac_pe: CellValue | None


@dataclass(slots=True)
class DistributionRow:
    """Row for distribution tables."""

    problem: ProblemInfo
    count: int
    pct: float


@dataclass(slots=True)
class TeamRow:
    """Row for the team-by-problem report."""

    team_display: str
    total_submissions: int
    accepted: CellValue
    cells: dict[str, CellValue] = field(default_factory=dict)


@dataclass(slots=True)
class TimeWindow:
    """One bar in the time-distribution charts."""

    label: str
    all_count: int
    accepted_count: int


@dataclass(slots=True)
class ContestReport:
    """All aggregated data for the reports page."""

    problems: list[ProblemInfo]
    languages: list[LanguageInfo]
    accept_pe: bool
    total_runs: int
    total_accepted: int
    problem_summary: list[ProblemSummaryRow]
    runs_distribution: list[DistributionRow]
    accepted_distribution: list[DistributionRow]
    problem_verdict: dict[str, dict[str, CellValue]]
    problem_verdict_totals: dict[str, CellValue]
    problem_language: dict[str, dict[str, CellValue]]
    problem_language_totals: dict[str, CellValue]
    language_verdict: dict[str, dict[str, CellValue]]
    language_verdict_totals: dict[str, CellValue]
    team_problem: list[TeamRow]
    time_windows: list[TimeWindow]
