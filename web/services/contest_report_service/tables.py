#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Table-building helpers for contest report aggregation."""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean, median, quantiles

from .common import ALL_VERDICTS, pct
from .models import (
    ActiveTeamsHighlight,
    CellValue,
    FiveNumberSummary,
    Highlights,
    LanguageHighlight,
    LanguageInfo,
    PerformanceSummary,
    ProblemHighlight,
    ProblemInfo,
    ProblemRaceSeries,
    SolvedCountBucket,
    SolveMetrics,
    TeamRow,
    TimeWindow,
)


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
    team_display_map: dict[str, tuple[str, str]],
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
        display, username = team_display_map[team_key]
        rows.append(
            TeamRow(
                team_display=display,
                team_username=username,
                total_submissions=total_submissions,
                accepted=CellValue(
                    count=team_accepted[team_key],
                    pct=pct(team_accepted[team_key], total_submissions),
                ),
                cells=cells,
            )
        )
    return rows


def build_solve_metrics(
    problem_infos: list[ProblemInfo],
    by_problem: defaultdict[str, int],
    attempting_teams: defaultdict[str, set[str]],
    team_problem_total: defaultdict[str, defaultdict[str, int]],
    solve_records: defaultdict[str, list[tuple[int, str]]],
    team_display_map: dict[str, tuple[str, str]],
    dirt_wrong_by_problem: defaultdict[str, int],
) -> dict[str, SolveMetrics]:
    """Build per-problem submission/solve-time metrics, keyed by problem label.

    ``solve_records[label]`` holds one ``(solved_at_minutes, team_id)`` pair per
    team that solved the problem -- its first accepted submission only -- so the
    median/average/first-solved figures are computed over solving teams alone.
    ``team_id`` resolves the first solver's display name through
    ``team_display_map`` (the same map ``build_team_rows`` uses).
    ``dirt_wrong_by_problem[label]`` is the pooled count of solving teams'
    non-accepted submissions before their solve; dividing it by itself plus
    the solver count (``len(records)``) gives the ICPC dirt ratio.
    """
    metrics: dict[str, SolveMetrics] = {}
    for problem in problem_infos:
        label = problem.label
        attempters = attempting_teams[label]
        attempter_count = len(attempters)
        avg_submissions = by_problem[label] / attempter_count if attempter_count else 0.0
        median_submissions = (
            median(team_problem_total[team_key][label] for team_key in attempters) if attempters else 0.0
        )
        records = solve_records[label]
        if records:
            times = [minutes for minutes, _ in records]
            first_solver_team_id = min(records, key=lambda record: record[0])[1]
            dirt_wrong = dirt_wrong_by_problem[label]
            dirt_total = dirt_wrong + len(records)
            metrics[label] = SolveMetrics(
                avg_submissions=avg_submissions,
                median_submissions=median_submissions,
                median_time_solved=median(times),
                avg_time_solved=mean(times),
                first_solved_minutes=min(times),
                first_solver_name=team_display_map[first_solver_team_id][0],
                dirt_ratio=pct(dirt_wrong, dirt_total),
            )
        else:
            metrics[label] = SolveMetrics(
                avg_submissions=avg_submissions,
                median_submissions=median_submissions,
                median_time_solved=None,
                avg_time_solved=None,
                first_solved_minutes=None,
                first_solver_name=None,
                dirt_ratio=None,
            )
    return metrics


def build_problem_race(
    problem_infos: list[ProblemInfo],
    solve_records: defaultdict[str, list[tuple[int, str]]],
) -> list[ProblemRaceSeries]:
    """Build the Problem Race chart's per-problem solve-time series.

    Reuses the same ``solve_records`` (first-accepted-submission minute per
    solving team) that grounds ``SolveMetrics`` and the Highlights cards, so
    the race line and the rest of the page can never disagree about who
    solved what, when.
    """
    return [
        ProblemRaceSeries(
            problem=problem,
            solved_minutes=sorted(minutes for minutes, _ in solve_records[problem.label]),
        )
        for problem in problem_infos
    ]


def _five_number_summary(values: list[int]) -> FiveNumberSummary | None:
    """Return Min/Q1/Median/Q3/Max/Mean for `values`, or None with fewer than 2."""
    if len(values) < 2:
        return None
    q1, med, q3 = quantiles(values, n=4)
    return FiveNumberSummary(
        minimum=min(values),
        q1=q1,
        median=med,
        q3=q3,
        maximum=max(values),
        mean=mean(values),
    )


def build_performance_summary(
    solved_count_by_team: dict[str, int],
    penalty_by_team: dict[str, int],
) -> PerformanceSummary:
    """Build the Performance section's solved-count and penalty distributions.

    Both dicts are keyed by every *active* team id, including teams with 0
    solves and thus 0 penalty minutes, so this always describes the same
    population as `Highlights.active_teams`.
    """
    solved_values = list(solved_count_by_team.values())
    penalty_values = [penalty_by_team.get(team_id, 0) for team_id in solved_count_by_team]

    histogram_counts: defaultdict[int, int] = defaultdict(int)
    for solved in solved_values:
        histogram_counts[solved] += 1
    max_solved = max(solved_values, default=0)
    solved_histogram = [SolvedCountBucket(solved=n, team_count=histogram_counts[n]) for n in range(max_solved + 1)]

    top_10pct_solved: int | None = None
    if len(solved_values) >= 2:
        # `quantiles(values, n=10)` returns the 9 decile cut points (10th,
        # 20th, ..., 90th percentile); index 8 is the 90th. The stdlib's
        # default "exclusive" method extrapolates past the observed extremes
        # on a small sample (e.g. a 5-team field can compute a 90th
        # percentile of 3.4 when nobody solved more than 3) -- clamped to
        # `max_solved` so the threshold is never an unreachable number.
        top_10pct_solved = min(math.ceil(quantiles(solved_values, n=10)[8]), max_solved)

    return PerformanceSummary(
        active_team_count=len(solved_count_by_team),
        solved_summary=_five_number_summary(solved_values),
        solved_histogram=solved_histogram,
        penalty_summary=_five_number_summary(penalty_values),
        top_10pct_solved=top_10pct_solved,
    )


def build_highlights(
    problem_infos: list[ProblemInfo],
    language_infos: list[LanguageInfo],
    solved_teams_by_problem: dict[str, int],
    language_totals: defaultdict[str, int],
    active_teams: int,
    enrolled_teams: int,
    total_runs: int,
    total_accepted: int,
) -> Highlights:
    """Build the top-of-page highlight cards.

    ``active_teams`` -- teams with at least one submission, judged or not --
    is the percentage denominator for ``most_solved``/``least_solved``, not
    the full enrolled roster: a team that never submitted must not deflate a
    problem's acceptance rate just because it skipped the contest.
    ``enrolled_teams`` is used only by the ``active_teams`` card itself, which
    is the one figure meant to answer "how many of the roster showed up".

    A tie on solved-team count lists every tied problem (``problem_infos``
    order, i.e. by ordinal) rather than picking one arbitrarily -- see
    `ProblemHighlight`. ``most_used_language`` is ``None`` only when the
    contest has no configured languages at all, which cannot happen alongside
    a judged submission but is handled defensively.
    """
    max_solved = max(solved_teams_by_problem[p.label] for p in problem_infos)
    most_solved_problems = [p for p in problem_infos if solved_teams_by_problem[p.label] == max_solved]

    # A problem nobody has accepted is reported on its own card rather than as
    # the extreme of this one -- see `Highlights`. What is left here is the
    # hardest problem that somebody did solve, so the minimum is taken over
    # solved problems only, and there is no such problem when the field has
    # solved nothing at all.
    unsolved_problems = [p for p in problem_infos if solved_teams_by_problem[p.label] == 0]
    solved_problems = [p for p in problem_infos if solved_teams_by_problem[p.label] > 0]
    least_solved: ProblemHighlight | None = None
    if solved_problems:
        min_solved = min(solved_teams_by_problem[p.label] for p in solved_problems)
        least_solved = ProblemHighlight(
            problems=[p for p in solved_problems if solved_teams_by_problem[p.label] == min_solved],
            solved_teams=min_solved,
            pct_of_teams=pct(min_solved, active_teams),
        )

    most_used_language: LanguageHighlight | None = None
    if language_infos:
        top_language = max(language_infos, key=lambda language: language_totals[language.id])
        top_count = language_totals[top_language.id]
        most_used_language = LanguageHighlight(
            language=top_language,
            count=top_count,
            pct=pct(top_count, total_runs),
        )

    return Highlights(
        most_solved=ProblemHighlight(
            problems=most_solved_problems,
            solved_teams=max_solved,
            pct_of_teams=pct(max_solved, active_teams),
        ),
        least_solved=least_solved,
        unsolved=unsolved_problems,
        most_used_language=most_used_language,
        global_acceptance_pct=pct(total_accepted, total_runs),
        active_teams=ActiveTeamsHighlight(
            active=active_teams,
            enrolled=enrolled_teams,
            pct=pct(active_teams, enrolled_teams),
        ),
    )


def build_time_windows(
    duration_minutes: int,
    window_all: defaultdict[int, int],
    window_accepted: defaultdict[int, int],
    window_minutes: int = 10,
) -> list[TimeWindow]:
    """Build histogram windows."""
    num_windows = max(math.ceil(duration_minutes / window_minutes), 1)
    if window_all:
        num_windows = max(num_windows, max(window_all.keys()) + 1)

    windows: list[TimeWindow] = []
    for index in range(num_windows):
        start = index * window_minutes
        windows.append(
            TimeWindow(
                label=f"{start}-{start + window_minutes}",
                all_count=window_all[index],
                accepted_count=window_accepted[index],
            )
        )
    return windows
