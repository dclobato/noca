#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Table-building helpers for contest report aggregation."""

from __future__ import annotations

import math
from collections import defaultdict

from .common import ALL_VERDICTS, pct
from .models import CellValue, LanguageInfo, ProblemInfo, TeamRow, TimeWindow


def build_problem_verdict_table(
    problem_infos: list[ProblemInfo],
    by_problem: defaultdict[str, int],
    problem_verdict: defaultdict[str, defaultdict[str, int]],
    total_runs: int,
) -> tuple[dict[str, dict[str, CellValue]], dict[str, CellValue]]:
    """Build the problem-by-verdict cross-table and totals."""
    table: dict[str, dict[str, CellValue]] = {}
    totals: dict[str, CellValue] = {}
    verdict_totals: defaultdict[str, int] = defaultdict(int)

    for problem in problem_infos:
        row_total = by_problem[problem.label]
        row: dict[str, CellValue] = {}
        for verdict in ALL_VERDICTS:
            count = problem_verdict[problem.label][verdict.value]
            row[verdict.value] = CellValue(count=count, pct=pct(count, row_total))
            verdict_totals[verdict.value] += count
        table[problem.label] = row

    for verdict in ALL_VERDICTS:
        totals[verdict.value] = CellValue(
            count=verdict_totals[verdict.value],
            pct=pct(verdict_totals[verdict.value], total_runs),
        )

    return table, totals


def build_problem_language_table(
    problem_infos: list[ProblemInfo],
    language_infos: list[LanguageInfo],
    by_problem: defaultdict[str, int],
    problem_language: defaultdict[str, defaultdict[str, int]],
    total_runs: int,
) -> tuple[dict[str, dict[str, CellValue]], dict[str, CellValue], defaultdict[str, int]]:
    """Build the problem-by-language cross-table and totals."""
    table: dict[str, dict[str, CellValue]] = {}
    totals: dict[str, CellValue] = {}
    language_totals: defaultdict[str, int] = defaultdict(int)

    for problem in problem_infos:
        row_total = by_problem[problem.label]
        row: dict[str, CellValue] = {}
        for language in language_infos:
            count = problem_language[problem.label][language.id]
            row[language.id] = CellValue(count=count, pct=pct(count, row_total))
            language_totals[language.id] += count
        table[problem.label] = row

    for language in language_infos:
        totals[language.id] = CellValue(
            count=language_totals[language.id],
            pct=pct(language_totals[language.id], total_runs),
        )

    return table, totals, language_totals


def build_language_verdict_table(
    language_infos: list[LanguageInfo],
    language_verdict: defaultdict[str, defaultdict[str, int]],
    language_totals: defaultdict[str, int],
    total_runs: int,
) -> tuple[dict[str, dict[str, CellValue]], dict[str, CellValue]]:
    """Build the language-by-verdict cross-table and totals."""
    table: dict[str, dict[str, CellValue]] = {}
    totals: dict[str, CellValue] = {}
    verdict_totals: defaultdict[str, int] = defaultdict(int)

    for language in language_infos:
        language_total = language_totals[language.id]
        row: dict[str, CellValue] = {}
        for verdict in ALL_VERDICTS:
            count = language_verdict[language.id][verdict.value]
            row[verdict.value] = CellValue(count=count, pct=pct(count, language_total))
            verdict_totals[verdict.value] += count
        table[language.id] = row

    for verdict in ALL_VERDICTS:
        totals[verdict.value] = CellValue(
            count=verdict_totals[verdict.value],
            pct=pct(verdict_totals[verdict.value], total_runs),
        )

    return table, totals


def build_team_rows(
    problem_infos: list[ProblemInfo],
    team_total: defaultdict[str, int],
    team_accepted: defaultdict[str, int],
    team_problem_total: defaultdict[str, defaultdict[str, int]],
    team_display_map: dict[str, str],
) -> list[TeamRow]:
    """Build rows for the team-by-problem report."""
    rows: list[TeamRow] = []
    for team_key in sorted(team_total, key=lambda key: team_total[key], reverse=True):
        total_submissions = team_total[team_key]
        cells = {
            problem.label: CellValue(
                count=team_problem_total[team_key][problem.label],
                pct=pct(team_problem_total[team_key][problem.label], total_submissions),
            )
            for problem in problem_infos
        }
        rows.append(
            TeamRow(
                team_display=team_display_map[team_key],
                total_submissions=total_submissions,
                accepted=CellValue(
                    count=team_accepted[team_key],
                    pct=pct(team_accepted[team_key], total_submissions),
                ),
                cells=cells,
            )
        )
    return rows


def build_time_windows(
    duration_minutes: int,
    window_all: defaultdict[int, int],
    window_accepted: defaultdict[int, int],
) -> list[TimeWindow]:
    """Build 10-minute histogram windows."""
    num_windows = max(math.ceil(duration_minutes / 10), 1)
    if window_all:
        num_windows = max(num_windows, max(window_all.keys()) + 1)

    windows: list[TimeWindow] = []
    for index in range(num_windows):
        start = index * 10
        windows.append(
            TimeWindow(
                label=f"{start}-{start + 10}",
                all_count=window_all[index],
                accepted_count=window_accepted[index],
            )
        )
    return windows
