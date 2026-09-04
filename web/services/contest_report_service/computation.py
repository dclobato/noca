#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest report aggregation logic."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from shared.enumerations import JudgmentStatus, Verdict
from shared.services.scoreboard_projection import penalizing_verdicts, submission_sort_key
from shared.timing import icpc_minutes_from_seconds
from web.services.assorted_utils import format_site_identity

from .common import ordinal_to_label, pct
from .models import (
    CellValue,
    ContestReport,
    DistributionRow,
    LanguageInfo,
    ProblemInfo,
    ProblemSummaryRow,
)
from .tables import (
    build_highlights,
    build_language_verdict_table,
    build_performance_summary,
    build_problem_language_table,
    build_problem_race,
    build_problem_verdict_table,
    build_solve_metrics,
    build_team_rows,
    build_time_windows,
)

if TYPE_CHECKING:
    from web.models.contest import Contest
    from web.models.language import Language
    from web.services.contest_report_query_service import ContestReportProblemRow, ContestReportSubmissionRow


def compute_contest_report(
    contest: Contest,
    submissions: list[ContestReportSubmissionRow],
    problems: list[ContestReportProblemRow],
    languages: list[Language],
    enrolled_teams: int,
) -> ContestReport:
    """Aggregate submissions into a contest report."""
    accept_pe = contest.accept_pe

    def is_accepted(verdict: Verdict) -> bool:
        return verdict == Verdict.AC or (accept_pe and verdict == Verdict.PE)

    # "Active" teams: those with at least one submission, judged or not. A
    # team that never showed up must not deflate an acceptance rate -- 80 of
    # 80 *active* teams solving a problem is 100%, not 80% just because 20
    # enrolled teams skipped the contest entirely.
    active_team_ids = {str(submission.team_id) for submission in submissions}
    active_teams = len(active_team_ids)

    wa_penalty = int(contest.wa_penalty)
    failed_verdicts = penalizing_verdicts(accept_pe, contest.ce_adds_penalty)

    judged: list[tuple[ContestReportSubmissionRow, Verdict]] = []
    for submission in submissions:
        if submission.judgment_status == JudgmentStatus.DONE and submission.final_verdict is not None:
            judged.append((submission, submission.final_verdict))
    # Chronological order is required below: a team's first accepted
    # submission decides `solve_records`, and `list_submissions` does not
    # guarantee chronological order (it defaults to newest-first).
    judged.sort(key=lambda item: submission_sort_key(item[0]))

    # The full contest problem set, not "problems with a judged submission in
    # `submissions`" -- a site whose teams never touched problem L must still
    # see L (with honest zeros), not have it silently vanish from every
    # table. `problems` is already ordered by ordinal.
    problem_infos = [
        ProblemInfo(
            label=ordinal_to_label(problem.ordinal),
            title=problem.title,
            color=(problem.color or "#888888").lstrip("#"),
        )
        for problem in problems
    ]
    pid_to_label = {problem.id: ordinal_to_label(problem.ordinal) for problem in problems}
    language_infos = [LanguageInfo(id=lang.id, name=lang.name, icon=lang.icon or "") for lang in languages]

    by_problem: defaultdict[str, int] = defaultdict(int)
    ac_by_problem: defaultdict[str, int] = defaultdict(int)
    acpe_by_problem: defaultdict[str, int] = defaultdict(int)
    problem_verdict: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    problem_language: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    language_verdict: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    team_total: defaultdict[str, int] = defaultdict(int)
    team_accepted: defaultdict[str, int] = defaultdict(int)
    team_problem_total: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    team_problem_accepted: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    team_display_map: dict[str, tuple[str, str]] = {}
    window_all: defaultdict[int, int] = defaultdict(int)
    window_accepted: defaultdict[int, int] = defaultdict(int)
    attempting_teams: defaultdict[str, set[str]] = defaultdict(set)
    solved_team_problem: set[tuple[str, str]] = set()
    solve_records: defaultdict[str, list[tuple[int, str]]] = defaultdict(list)
    attempts_before_solve: defaultdict[tuple[str, str], int] = defaultdict(int)
    dirt_wrong_by_problem: defaultdict[str, int] = defaultdict(int)
    # Official contest penalty (respects accept_pe/ce_adds_penalty), tracked
    # separately from `attempts_before_solve` above: dirt counts *every*
    # non-accepted submission as "wrong", while contest penalty must only
    # count the verdicts the contest itself penalizes.
    penalty_attempts_before_solve: defaultdict[tuple[str, str], int] = defaultdict(int)
    solve_minute_by_team_problem: dict[tuple[str, str], int] = {}

    total_runs = 0
    total_acpe = 0

    for submission, verdict in judged:
        label = pid_to_label[str(submission.problem_id)]
        language_id = str(submission.language_id)
        total_runs += 1

        by_problem[label] += 1
        problem_verdict[label][verdict.value] += 1
        problem_language[label][language_id] += 1
        language_verdict[language_id][verdict.value] += 1

        accepted = is_accepted(verdict)
        if verdict == Verdict.AC:
            ac_by_problem[label] += 1
        if accepted:
            total_acpe += 1
            acpe_by_problem[label] += 1

        team_key = str(submission.team_id)
        if team_key not in team_display_map:
            base = submission.team_fullname or submission.team_username
            team_display_map[team_key] = (
                format_site_identity(submission.team_site_name, base),
                submission.team_username,
            )
        team_total[team_key] += 1
        team_problem_total[team_key][label] += 1
        if accepted:
            team_accepted[team_key] += 1
            team_problem_accepted[team_key][label] += 1

        attempting_teams[label].add(team_key)
        timestamp_seconds = submission.timestamp_seconds
        solve_key = (team_key, label)
        if solve_key not in solved_team_problem:
            if accepted:
                solved_team_problem.add(solve_key)
                if timestamp_seconds is not None and timestamp_seconds >= 0:
                    solved_at_minutes = icpc_minutes_from_seconds(timestamp_seconds)
                    if solved_at_minutes is not None:
                        solve_records[label].append((solved_at_minutes, team_key))
                        solve_minute_by_team_problem[solve_key] = solved_at_minutes
                        # Dirt ratio (ICPC resolver metric): the share of
                        # solving teams' submissions that were wrong, pooled
                        # across every solver rather than averaged per team --
                        # counted here, not per attempt, so it stays in step
                        # with which teams actually made it into `solve_records`.
                        dirt_wrong_by_problem[label] += attempts_before_solve[solve_key]
            else:
                attempts_before_solve[solve_key] += 1
                if verdict in failed_verdicts:
                    penalty_attempts_before_solve[solve_key] += 1

        if timestamp_seconds is not None and timestamp_seconds >= 0:
            window_index = timestamp_seconds // 600
            window_all[window_index] += 1
            if accepted:
                window_accepted[window_index] += 1

    solve_metrics = build_solve_metrics(
        problem_infos,
        by_problem,
        attempting_teams,
        team_problem_total,
        solve_records,
        team_display_map,
        dirt_wrong_by_problem,
    )

    problem_summary = [
        ProblemSummaryRow(
            problem=problem,
            total_runs=by_problem[problem.label],
            ac=CellValue(
                count=ac_by_problem[problem.label],
                pct=pct(ac_by_problem[problem.label], by_problem[problem.label]),
            ),
            ac_pe=(
                CellValue(
                    count=acpe_by_problem[problem.label],
                    pct=pct(acpe_by_problem[problem.label], by_problem[problem.label]),
                )
                if accept_pe
                else None
            ),
            solve_metrics=solve_metrics[problem.label],
        )
        for problem in problem_infos
    ]

    runs_distribution = [
        DistributionRow(
            problem=problem,
            count=by_problem[problem.label],
            pct=pct(by_problem[problem.label], total_runs),
        )
        for problem in problem_infos
    ]

    # Distinct solving teams per problem, not accepted *submissions*: a team
    # that submits AC twice to the same problem (a cleaner rewrite, a second
    # language) must not inflate this distribution, unlike the Runs
    # Distribution above which is deliberately submission-volume-based.
    solved_teams_by_problem = {problem.label: len(solve_records[problem.label]) for problem in problem_infos}
    total_solves = sum(solved_teams_by_problem.values())
    accepted_distribution = [
        DistributionRow(
            problem=problem,
            count=solved_teams_by_problem[problem.label],
            pct=pct(solved_teams_by_problem[problem.label], total_solves),
        )
        for problem in problem_infos
    ]

    problem_verdict_table, problem_verdict_totals = build_problem_verdict_table(
        problem_infos,
        by_problem,
        problem_verdict,
        total_runs,
    )
    problem_language_table, problem_language_totals, language_totals = build_problem_language_table(
        problem_infos,
        language_infos,
        by_problem,
        problem_language,
        total_runs,
    )
    language_verdict_table, language_verdict_totals = build_language_verdict_table(
        language_infos,
        language_verdict,
        language_totals,
        total_runs,
    )
    team_rows = build_team_rows(problem_infos, team_total, team_accepted, team_problem_total, team_display_map)
    time_windows = build_time_windows(contest.duration_minutes, window_all, window_accepted)
    problem_race = build_problem_race(problem_infos, solve_records)

    # Per-team solved count and ICPC penalty minutes, over every active team
    # (0 for a team that solved nothing) -- the Performance section's
    # population. Iterating `solve_minute_by_team_problem` rather than
    # `solved_team_problem` keeps this in the same population as
    # `solve_records` (a solve whose minute could not be computed is excluded
    # from both, consistently).
    solved_count_by_team: dict[str, int] = dict.fromkeys(active_team_ids, 0)
    penalty_by_team: dict[str, int] = dict.fromkeys(active_team_ids, 0)
    for (team_key, label), solve_minute in solve_minute_by_team_problem.items():
        solved_count_by_team[team_key] += 1
        penalty_by_team[team_key] += solve_minute + penalty_attempts_before_solve[(team_key, label)] * wa_penalty
    performance = build_performance_summary(solved_count_by_team, penalty_by_team)

    highlights = build_highlights(
        problem_infos,
        language_infos,
        solved_teams_by_problem,
        language_totals,
        active_teams,
        enrolled_teams,
        total_runs,
        total_acpe,
    )

    return ContestReport(
        problems=problem_infos,
        languages=language_infos,
        accept_pe=accept_pe,
        total_runs=total_runs,
        total_accepted=total_acpe,
        problem_summary=problem_summary,
        runs_distribution=runs_distribution,
        accepted_distribution=accepted_distribution,
        problem_verdict=problem_verdict_table,
        problem_verdict_totals=problem_verdict_totals,
        problem_language=problem_language_table,
        problem_language_totals=problem_language_totals,
        language_verdict=language_verdict_table,
        language_verdict_totals=language_verdict_totals,
        team_problem=team_rows,
        time_windows=time_windows,
        problem_race=problem_race,
        highlights=highlights,
        performance=performance,
    )
